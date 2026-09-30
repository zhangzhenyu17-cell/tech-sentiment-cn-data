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

V2 prefers a private GitHub App installed only on
zhangzhenyu17-cell/tech-sentiment-cn-data with repository permission
Actions: Read and write. The Worker mints a short-lived installation token at
runtime and explicitly scopes each token request to that repository and
actions=write.

Cloudflare stores two App credentials as encrypted Worker secrets:

- GITHUB_APP_ID
- GITHUB_APP_PRIVATE_KEY (PKCS#8 PEM)

GitHub-generated private keys are normally downloaded as RSA PEM. Convert the
downloaded key to unencrypted PKCS#8 before storing it:

openssl pkcs8 -topk8 -nocrypt -in github-app.pem -out github-app-pkcs8.pem

During migration only, the existing repository-scoped GITHUB_TOKEN PAT remains
available as a fallback. After one real Cron invocation proves
github_auth_mode=github_app, remove GITHUB_TOKEN and revoke the PAT.

Do not install the App on zhangzhenyu17-cell/tech-sentiment-cn. Do not grant
Contents write, Administration, Secrets, Environments, Production, or trading
permissions. Never commit the App private key or any installation token.

## Local verification

Run:

node --test test/worker.test.mjs

npx wrangler deploy --dry-run

## Deployment

Authenticate Wrangler to the user's Cloudflare account with npx wrangler login.

Create a private GitHub App with only Actions: Read and write, install it only
on tech-sentiment-cn-data, generate a private key, and convert that key to
PKCS#8. Store the App ID and converted key as Cloudflare encrypted secrets:

npx wrangler secret put GITHUB_APP_ID
npx wrangler secret put GITHUB_APP_PRIVATE_KEY

Keep GITHUB_TOKEN only during the transition. Then deploy with:

npx wrangler deploy

This Worker is scheduled-only with workers_dev=false, so no public workers.dev
route is required. Inspect a real Cron invocation and require
github_auth_mode=github_app before deleting GITHUB_TOKEN and revoking the PAT.
A healthy normal cycle should usually log NOOP_RECENT_GITHUB_DELIVERY. When
GitHub schedule delivery is absent beyond the threshold it should log
DISPATCHED_ORCHESTRATOR.

## Safety invariants

- no historical backfill;
- no forward-outcome read;
- no evidence-qualification change;
- no model, threshold, or universe change;
- no Production promotion;
- no trading-authority change;
- no private-repository credential or access.
