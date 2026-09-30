const SHANGHAI_OFFSET_MS = 8 * 60 * 60 * 1000;
const ACTIVE_START_MINUTE = 15 * 60 + 15;
const ACTIVE_END_MINUTE = 9 * 60 + 5;

function asPositiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function shanghaiMinuteOfDay(epochMs) {
  const shifted = new Date(epochMs + SHANGHAI_OFFSET_MS);
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
}

export function inFormalWrapperWindow(epochMs) {
  const minute = shanghaiMinuteOfDay(epochMs);
  return minute >= ACTIVE_START_MINUTE || minute <= ACTIVE_END_MINUTE;
}

function base64Url(input) {
  const bytes =
    typeof input === "string" ? new TextEncoder().encode(input) : input;
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function pkcs8PemToArrayBuffer(pem) {
  if (!pem.includes("BEGIN PRIVATE KEY")) {
    throw new Error("GITHUB_APP_PRIVATE_KEY must be PKCS#8 PEM");
  }
  const body = pem
    .replace("-----BEGIN PRIVATE KEY-----", "")
    .replace("-----END PRIVATE KEY-----", "")
    .replace(/\s+/g, "");
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

export async function createGitHubAppJwt({
  appId,
  privateKeyPem,
  nowMs = Date.now(),
}) {
  const nowSeconds = Math.floor(nowMs / 1000);
  const header = base64Url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = base64Url(
    JSON.stringify({
      iat: nowSeconds - 60,
      exp: nowSeconds + 540,
      iss: String(appId),
    }),
  );
  const signingInput = header + "." + payload;
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pkcs8PemToArrayBuffer(privateKeyPem),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    key,
    new TextEncoder().encode(signingInput),
  );
  return signingInput + "." + base64Url(new Uint8Array(signature));
}

function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: "Bearer " + token,
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": "prospective-external-watchdog-v2",
  };
}

function configFromEnv(env) {
  return {
    owner: env.GITHUB_OWNER || "zhangzhenyu17-cell",
    repo: env.GITHUB_REPO || "tech-sentiment-cn-data",
    workflow:
      env.ORCHESTRATOR_WORKFLOW ||
      "prospective-public-daily-orchestrator-v1.yml",
    ref: env.GITHUB_REF || "main",
    gapMinutes: asPositiveNumber(env.WATCHDOG_GAP_MINUTES, 25),
    dryRun: String(env.DRY_RUN || "false").toLowerCase() === "true",
  };
}

async function githubJson(fetchImpl, url, token, init = {}) {
  const response = await fetchImpl(url, {
    ...init,
    headers: {
      ...githubHeaders(token),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      "GITHUB_API_ERROR status=" +
        response.status +
        " url=" +
        url +
        " body=" +
        body.slice(0, 500),
    );
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function resolveGitHubAuth(fetchImpl, cfg, env, nowMs) {
  const hasAppId = Boolean(env.GITHUB_APP_ID);
  const hasAppKey = Boolean(env.GITHUB_APP_PRIVATE_KEY);

  if (hasAppId !== hasAppKey) {
    throw new Error(
      "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be configured together",
    );
  }

  if (hasAppId && hasAppKey) {
    try {
      const jwt = await createGitHubAppJwt({
        appId: env.GITHUB_APP_ID,
        privateKeyPem: env.GITHUB_APP_PRIVATE_KEY,
        nowMs,
      });
      const installation = await githubJson(
        fetchImpl,
        "https://api.github.com/repos/" +
          cfg.owner +
          "/" +
          cfg.repo +
          "/installation",
        jwt,
      );
      if (!installation?.id) {
        throw new Error("GitHub App installation was not found for target repo");
      }
      const tokenPayload = await githubJson(
        fetchImpl,
        "https://api.github.com/app/installations/" +
          installation.id +
          "/access_tokens",
        jwt,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            repositories: [cfg.repo],
            permissions: { actions: "write" },
          }),
        },
      );
      if (!tokenPayload?.token) {
        throw new Error("GitHub App installation token was not returned");
      }
      return {
        token: tokenPayload.token,
        mode: "github_app",
        installationId: installation.id,
      };
    } catch (error) {
      if (!env.GITHUB_TOKEN) throw error;
      return {
        token: env.GITHUB_TOKEN,
        mode: "pat_fallback",
        appError: String(error?.message || error),
      };
    }
  }

  if (env.GITHUB_TOKEN) {
    return {
      token: env.GITHUB_TOKEN,
      mode: "pat_legacy",
      installationId: null,
    };
  }

  throw new Error(
    "GitHub auth unavailable: configure GitHub App credentials or GITHUB_TOKEN",
  );
}

async function listOrchestratorRuns(fetchImpl, cfg, token) {
  const workflow = encodeURIComponent(cfg.workflow);
  const url = new URL(
    "https://api.github.com/repos/" +
      cfg.owner +
      "/" +
      cfg.repo +
      "/actions/workflows/" +
      workflow +
      "/runs",
  );
  url.searchParams.set("branch", cfg.ref);
  url.searchParams.set("per_page", "20");
  const payload = await githubJson(fetchImpl, url.toString(), token);
  return Array.isArray(payload?.workflow_runs) ? payload.workflow_runs : [];
}

async function dispatchOrchestrator(fetchImpl, cfg, token) {
  const workflow = encodeURIComponent(cfg.workflow);
  const url =
    "https://api.github.com/repos/" +
    cfg.owner +
    "/" +
    cfg.repo +
    "/actions/workflows/" +
    workflow +
    "/dispatches";
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      ...githubHeaders(token),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: cfg.ref,
      inputs: {
        trigger_source: "EXTERNAL_WATCHDOG_V2",
      },
    }),
  });
  if (![200, 201, 204].includes(response.status)) {
    const body = await response.text();
    throw new Error(
      "GITHUB_DISPATCH_ERROR status=" +
        response.status +
        " body=" +
        body.slice(0, 500),
    );
  }
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function newestCreatedAt(runs) {
  let newest = null;
  for (const run of runs) {
    const value = Date.parse(run.created_at || "");
    if (!Number.isFinite(value)) continue;
    if (newest === null || value > newest) newest = value;
  }
  return newest;
}

export async function runWatchdog({ nowMs, env, fetchImpl = fetch }) {
  const cfg = configFromEnv(env);
  const base = {
    schema_version: "prospective-external-watchdog-v2",
    checked_at_utc: new Date(nowMs).toISOString(),
    github_repository: cfg.owner + "/" + cfg.repo,
    workflow: cfg.workflow,
    ref: cfg.ref,
    watchdog_gap_minutes: cfg.gapMinutes,
    evidence_qualification_changed: false,
    historical_backfill_allowed: false,
    forward_outcomes_read: false,
    production_changed: false,
    trading_authority_changed: false,
  };

  if (!inFormalWrapperWindow(nowMs)) {
    return {
      ...base,
      github_auth_mode: "not_required",
      action: "NOOP_OUTSIDE_FORMAL_WRAPPER_WINDOW",
    };
  }

  const auth = await resolveGitHubAuth(fetchImpl, cfg, env, nowMs);
  const authState = {
    github_auth_mode: auth.mode,
    github_app_installation_id: auth.installationId ?? null,
  };

  const runs = await listOrchestratorRuns(fetchImpl, cfg, auth.token);
  const active = runs.find(
    (run) => run.status === "queued" || run.status === "in_progress",
  );
  if (active) {
    return {
      ...base,
      ...authState,
      action: "NOOP_ACTIVE_ORCHESTRATOR",
      active_run_id: active.id,
      active_run_status: active.status,
      active_run_url: active.html_url,
    };
  }

  const newest = newestCreatedAt(runs);
  const ageMinutes =
    newest === null ? null : Math.max(0, (nowMs - newest) / 60000);
  if (ageMinutes !== null && ageMinutes <= cfg.gapMinutes) {
    const latest = runs
      .filter((run) => Date.parse(run.created_at || "") === newest)
      .at(0);
    return {
      ...base,
      ...authState,
      action: "NOOP_RECENT_GITHUB_DELIVERY",
      latest_run_id: latest?.id ?? null,
      latest_run_status: latest?.status ?? null,
      latest_run_conclusion: latest?.conclusion ?? null,
      latest_run_event: latest?.event ?? null,
      latest_run_created_at: latest?.created_at ?? null,
      latest_run_age_minutes: Number(ageMinutes.toFixed(2)),
      latest_run_url: latest?.html_url ?? null,
    };
  }

  if (cfg.dryRun) {
    return {
      ...base,
      ...authState,
      action: "DRY_RUN_WOULD_DISPATCH_ORCHESTRATOR",
      latest_run_age_minutes:
        ageMinutes === null ? null : Number(ageMinutes.toFixed(2)),
    };
  }

  const dispatched = await dispatchOrchestrator(
    fetchImpl,
    cfg,
    auth.token,
  );
  return {
    ...base,
    ...authState,
    action: "DISPATCHED_ORCHESTRATOR",
    latest_run_age_minutes:
      ageMinutes === null ? null : Number(ageMinutes.toFixed(2)),
    dispatched_workflow_run_id: dispatched?.workflow_run_id ?? null,
    dispatched_run_url: dispatched?.html_url ?? null,
  };
}

export default {
  async scheduled(controller, env, ctx) {
    const nowMs = Number(controller.scheduledTime || Date.now());
    ctx.waitUntil(
      runWatchdog({ nowMs, env })
        .then((result) => console.log(JSON.stringify(result)))
        .catch((error) => {
          console.error(
            JSON.stringify({
              schema_version: "prospective-external-watchdog-v2",
              action: "WATCHDOG_ERROR",
              checked_at_utc: new Date(nowMs).toISOString(),
              error: String(error?.message || error),
              evidence_qualification_changed: false,
              historical_backfill_allowed: false,
              forward_outcomes_read: false,
              production_changed: false,
              trading_authority_changed: false,
            }),
          );
          throw error;
        }),
    );
  },

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({
        service: "prospective-external-watchdog-v2",
        status: "READY",
        role: "SCHEDULE_DELIVERY_ONLY",
        business_logic_authority: false,
        trading_calendar_authority: false,
        evidence_qualification_authority: false,
        production_authority: false,
        trading_authority: false,
      });
    }
    return new Response("Not Found", { status: 404 });
  },
};
