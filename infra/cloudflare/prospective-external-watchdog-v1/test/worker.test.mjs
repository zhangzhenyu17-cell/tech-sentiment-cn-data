import assert from "node:assert/strict";
import test from "node:test";

import {
  createGitHubAppJwt,
  inFormalWrapperWindow,
  runWatchdog,
  shanghaiMinuteOfDay,
} from "../src/worker.mjs";

const env = {
  GITHUB_TOKEN: "test-token",
  GITHUB_OWNER: "zhangzhenyu17-cell",
  GITHUB_REPO: "tech-sentiment-cn-data",
  ORCHESTRATOR_WORKFLOW: "prospective-public-daily-orchestrator-v1.yml",
  GITHUB_REF: "main",
  WATCHDOG_GAP_MINUTES: "25",
  DRY_RUN: "false",
};

function epoch(shanghaiIso) {
  return Date.parse(shanghaiIso);
}

function base64UrlDecodeJson(part) {
  const normalized = part.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  return JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
}

async function generatePkcs8Pem() {
  const pair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"],
  );
  const der = Buffer.from(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
  const body = der.toString("base64").match(/.{1,64}/g).join("\n");
  return "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----\n";
}

test("Asia/Shanghai wrapper window is bounded at 15:15 through 09:05", () => {
  assert.equal(
    inFormalWrapperWindow(epoch("2026-09-30T15:14:00+08:00")),
    false,
  );
  assert.equal(
    inFormalWrapperWindow(epoch("2026-09-30T15:15:00+08:00")),
    true,
  );
  assert.equal(
    inFormalWrapperWindow(epoch("2026-10-01T09:05:00+08:00")),
    true,
  );
  assert.equal(
    inFormalWrapperWindow(epoch("2026-10-01T09:06:00+08:00")),
    false,
  );
  assert.equal(
    shanghaiMinuteOfDay(epoch("2026-10-01T08:45:00+08:00")),
    8 * 60 + 45,
  );
});

test("GitHub App JWT uses RS256 and bounded GitHub claims", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  const privateKeyPem = await generatePkcs8Pem();
  const jwt = await createGitHubAppJwt({
    appId: "123456",
    privateKeyPem,
    nowMs,
  });
  const [headerPart, payloadPart, signaturePart] = jwt.split(".");
  const header = base64UrlDecodeJson(headerPart);
  const payload = base64UrlDecodeJson(payloadPart);

  assert.equal(header.alg, "RS256");
  assert.equal(header.typ, "JWT");
  assert.equal(payload.iss, "123456");
  assert.equal(payload.iat, Math.floor(nowMs / 1000) - 60);
  assert.equal(payload.exp, Math.floor(nowMs / 1000) + 540);
  assert.ok(signaturePart.length > 100);
});

test("GitHub App auth is preferred and scoped to target repo/actions write", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  const privateKeyPem = await generatePkcs8Pem();
  const appEnv = {
    ...env,
    GITHUB_APP_ID: "123456",
    GITHUB_APP_PRIVATE_KEY: privateKeyPem,
  };
  const requests = [];
  const fetchImpl = async (url, init = {}) => {
    const target = String(url);
    requests.push({ target, init });
    if (target.endsWith("/repos/zhangzhenyu17-cell/tech-sentiment-cn-data/installation")) {
      assert.match(init.headers.Authorization, /^Bearer eyJ/);
      return new Response(JSON.stringify({ id: 77 }), { status: 200 });
    }
    if (target.endsWith("/app/installations/77/access_tokens")) {
      const body = JSON.parse(init.body);
      assert.equal(init.method, "POST");
      assert.deepEqual(body, {
        repositories: ["tech-sentiment-cn-data"],
        permissions: { actions: "write" },
      });
      return new Response(JSON.stringify({ token: "installation-token" }), {
        status: 201,
      });
    }
    assert.equal(init.headers.Authorization, "Bearer installation-token");
    return new Response(
      JSON.stringify({
        workflow_runs: [
          {
            id: 81,
            status: "completed",
            conclusion: "success",
            event: "schedule",
            created_at: "2026-09-30T12:25:00Z",
            html_url: "https://example/run/81",
          },
        ],
      }),
      { status: 200 },
    );
  };

  const result = await runWatchdog({ nowMs, env: appEnv, fetchImpl });
  assert.equal(result.action, "NOOP_RECENT_GITHUB_DELIVERY");
  assert.equal(result.github_auth_mode, "github_app");
  assert.equal(result.github_app_installation_id, 77);
  assert.equal(requests.length, 3);
});

test("GitHub App auth can temporarily fall back to legacy PAT during migration", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  const privateKeyPem = await generatePkcs8Pem();
  const appEnv = {
    ...env,
    GITHUB_APP_ID: "123456",
    GITHUB_APP_PRIVATE_KEY: privateKeyPem,
  };
  let calls = 0;
  const fetchImpl = async (_url, init = {}) => {
    calls += 1;
    if (calls === 1) {
      return new Response(JSON.stringify({ message: "app unavailable" }), {
        status: 500,
      });
    }
    assert.equal(init.headers.Authorization, "Bearer test-token");
    return new Response(
      JSON.stringify({
        workflow_runs: [
          {
            id: 82,
            status: "completed",
            conclusion: "success",
            event: "schedule",
            created_at: "2026-09-30T12:25:00Z",
            html_url: "https://example/run/82",
          },
        ],
      }),
      { status: 200 },
    );
  };

  const result = await runWatchdog({ nowMs, env: appEnv, fetchImpl });
  assert.equal(result.github_auth_mode, "pat_fallback");
  assert.equal(result.action, "NOOP_RECENT_GITHUB_DELIVERY");
  assert.equal(calls, 2);
});

test("recent orchestrator delivery is a no-op regardless of event type", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  let calls = 0;
  const fetchImpl = async (_url, init = {}) => {
    calls += 1;
    assert.notEqual(init.method, "POST");
    return new Response(
      JSON.stringify({
        workflow_runs: [
          {
            id: 1,
            status: "completed",
            conclusion: "success",
            event: "schedule",
            created_at: "2026-09-30T12:25:00Z",
            html_url: "https://example/run/1",
          },
        ],
      }),
      { status: 200 },
    );
  };
  const result = await runWatchdog({ nowMs, env, fetchImpl });
  assert.equal(result.action, "NOOP_RECENT_GITHUB_DELIVERY");
  assert.equal(calls, 1);
});

test("active orchestrator suppresses external dispatch", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  const fetchImpl = async () =>
    new Response(
      JSON.stringify({
        workflow_runs: [
          {
            id: 2,
            status: "in_progress",
            conclusion: null,
            event: "workflow_dispatch",
            created_at: "2026-09-30T11:00:00Z",
            html_url: "https://example/run/2",
          },
        ],
      }),
      { status: 200 },
    );
  const result = await runWatchdog({ nowMs, env, fetchImpl });
  assert.equal(result.action, "NOOP_ACTIVE_ORCHESTRATOR");
});

test("missing GitHub delivery dispatches only the existing public orchestrator", async () => {
  const nowMs = epoch("2026-09-30T20:40:00+08:00");
  const requests = [];
  const fetchImpl = async (url, init = {}) => {
    requests.push({ url: String(url), init });
    if ((init.method || "GET") === "POST") {
      const body = JSON.parse(init.body);
      assert.equal(body.ref, "main");
      assert.deepEqual(body.inputs, {
        trigger_source: "EXTERNAL_WATCHDOG_V2",
      });
      return new Response(
        JSON.stringify({
          workflow_run_id: 123,
          html_url: "https://github.com/example/actions/runs/123",
        }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        workflow_runs: [
          {
            id: 9,
            status: "completed",
            conclusion: "success",
            event: "schedule",
            created_at: "2026-09-30T10:00:00Z",
            html_url: "https://example/run/9",
          },
        ],
      }),
      { status: 200 },
    );
  };

  const result = await runWatchdog({ nowMs, env, fetchImpl });
  assert.equal(result.action, "DISPATCHED_ORCHESTRATOR");
  assert.equal(requests.length, 2);
  assert.match(
    requests[1].url,
    /prospective-public-daily-orchestrator-v1\.yml\/dispatches$/,
  );
});

test("outside wrapper window performs no GitHub request", async () => {
  const nowMs = epoch("2026-09-30T12:00:00+08:00");
  const fetchImpl = async () => {
    throw new Error("fetch must not be called");
  };
  const result = await runWatchdog({ nowMs, env, fetchImpl });
  assert.equal(result.action, "NOOP_OUTSIDE_FORMAL_WRAPPER_WINDOW");
});
