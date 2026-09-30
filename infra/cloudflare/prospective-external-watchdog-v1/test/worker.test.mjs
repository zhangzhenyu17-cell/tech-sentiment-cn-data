import assert from "node:assert/strict";
import test from "node:test";

import {
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
        trigger_source: "EXTERNAL_WATCHDOG_V1",
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
