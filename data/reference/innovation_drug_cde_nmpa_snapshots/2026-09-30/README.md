# Innovation Drug CDE/NMPA official snapshot — 2026-09-30

This directory contains a real public CDE/NMPA snapshot captured at `2026-09-30T17:29:59+08:00` through a local real Chrome CDP session on the official CDE site.

Scope: official company query for `江苏恒瑞医药股份有限公司`, with exhaustive pagination for the four frozen source categories. `COMPLETE` is query-scoped and does not claim full-category completeness across all applicants.

Materialized result: 670 raw rows; all 670 rows exact-map to `600276.SH` under whole-field or delimiter-split exact legal-name token semantics (268 whole-field exact + 402 multi-applicant exact-token); 0 unmapped rows; 79 historically reconstructable explicit-date rows; 591 prospective-first-observed rows. Formal sector KPI remains `DATA_INSUFFICIENT`. No outcome read, direction, weight, sector/company score, evidence promotion, Production permission, or trading authority is created by this snapshot.

Mapping correction note: the original V1.2 materialization required the entire applicant field to equal the listed issuer legal name. V1.3 preserves that rule for single-applicant rows and additionally allows an exact legal-name token after splitting the official multi-applicant field on frozen delimiters; substring matching and affiliate inference remain forbidden. All 268 previously emitted event IDs are preserved byte-for-byte as identities, with 402 newly recovered source-event identities.
