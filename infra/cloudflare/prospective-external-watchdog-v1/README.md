# Prospective External Watchdog V1

This Cloudflare Worker is an independent schedule-delivery watchdog for the
public Formal Prospective V3 rail.

It does not implement market-data capture, the A-share trading calendar,
T/T+1 resolution, source qualification, PIT/freshness rules, public raw
assembly, private qualification, model logic, Production logic, or trading
authority.

Its only permitted write action is workflow_dispatch of
prospective-public-daily-orchestrator-v1.yml on main.

The existing public orchestrator remains authoritative for exact T/T+1
resolution, active-run guards, immutable source checks, collector dispatch,
and public raw assembly.

## Trigger policy

Cloudflare Cron is independent of GitHub Actions schedule.

The Worker checks the GitHub orchestrator during the broad wall-clock window
15:15 through 09:05 Asia/Shanghai. It does not maintain its own A-share
trading calendar.

Baseline watchdog observations occur every 15 minutes with a phase offset from
the native GitHub cron. Observations increase to every five minutes from 08:30
through 09:05.

If an orchestrator run is queued or in progress, the Worker does nothing. If
any orchestrator run was created within the last 25 minutes, the Worker does
nothing. Only when no active or recent delivery exists does it dispatch the
existing public orchestrator.

Any late GitHub native schedule delivery after an external dispatch remains
safe because the orchestrator and downstream collectors retain their existing
duplicate guards.

## Security boundary

GITHUB_TOKEN must be a GitHub fine-grained token restricted to repository
zhangzhenyu17-cell/tech-sentiment-cn-data with repository permission
Actions: Read and write.

Do not grant access to zhangzhenyu17-cell/tech-sentiment-cn. Do not grant
Contents write, Administration, Secrets, Environments, Production, or trading
permissions.

Store the token only as the Cloudflare encrypted Worker secret GITHUB_TOKEN.
Never commit it or place it in Wrangler vars.

## Local verification

Run:

node --test test/worker.test.mjs

npx wrangler deploy --dry-run

## Deployment

Authenticate Wrangler to the user's Cloudflare account with npx wrangler login.

After a scoped GitHub token has been created, store it with:

npx wrangler secret put GITHUB_TOKEN

Then deploy with:

npx wrangler deploy

This Worker is scheduled-only with workers_dev=false, so no public workers.dev
route is required. Inspect the first Cron invocation in Cloudflare logs. A healthy
normal cycle should usually log NOOP_RECENT_GITHUB_DELIVERY. When GitHub schedule delivery
is absent beyond the threshold it should log DISPATCHED_ORCHESTRATOR.

## Safety invariants

- no historical backfill;
- no forward-outcome read;
- no evidence-qualification change;
- no model, threshold, or universe change;
- no Production promotion;
- no trading-authority change;
- no private-repository credential or access.
