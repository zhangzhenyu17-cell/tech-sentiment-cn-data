# V4-A trading-calendar provider reliability fix

Date: 2026-09-18

Scope: mechanical public-data reliability fix only.

The first two `qualify-capital-inputs` attempts failed before any qualification artifact was produced because the default CSI 800 calendar carrier (`000906`) used the legacy AKShare Eastmoney code-discovery path and the remote endpoint repeatedly closed the connection.

The workflow now explicitly uses STAR 50 (`000688`) only as the trading-day calendar carrier for the 2022-01-04+ materialization steps. `000688` already uses the repository's existing Tencent-first / direct-Eastmoney fallback path. Downstream qualification still consumes exact observed market dates, performs no interpolation/forward-fill/backfill, and continues to fail closed when SSE/SZSE bilateral observations or other required inputs are missing.

This change does not alter ETF-share coverage thresholds, liquidity normalization, financing units/role, PIT availability/revision semantics, model logic, research scope, evidence qualification, Production, positions, or trading permissions.
