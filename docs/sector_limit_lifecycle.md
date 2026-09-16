# Sector V0 lifecycle special-day semantics

This public-input layer closes part of the `special_day_status` gap without using price outcomes.

Inputs:
- BaoStock daily `tradestatus` and `isST`;
- BaoStock `query_stock_basic()` lifecycle fields `ipoDate` / `outDate`;
- board/date exchange rules already encoded in `sector_limit_semantics.py`;
- optional explicit special-day overrides with a durable source URL.

Rules kept fail-closed:
- STAR and post-2020-08-24 ChiNext IPO first five **trading sessions** are marked `no_limit`;
- main-board listing day and pre-reform ChiNext listing day are marked `unknown`, not ordinary 10%/5%;
- unsupported boards, dates before IPO, and dates at/after reported outDate are `unknown`;
- relisting, delisting-transition and other exceptional dates require explicit overrides;
- no price move is inspected to infer a rule.

The combined `sector_limit_pipeline.py` feeds these statuses into the existing structural rule layer and the preregistered daily coverage gate (default 95%). This is research-input qualification only and changes no model parameter, event definition, production permission, or trading authority.
