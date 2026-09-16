# Secondary membership-evidence policy

This policy governs research-only use of public secondary sources for sector historical membership when first-party historical files are unavailable or incomplete.

## Admissible role

Secondary sources may qualify historical-research input only when they are reproducible, dated and independently cross-checked. They do not alter any model rule, threshold, portfolio policy or production permission.

For 931152 the current secondary rails are:

- EastMoney/Tiantian Fund historical disclosure for tracking ETF 159992: candidate constituent pool / dated disclosure anchor only.
- Sina stock related-index intervals: dated add/remove membership interval cross-check.

A fund holding is not automatically an index constituent. Q1/Q3 top holdings are cross-check only. Q2/Q4 disclosures with more than ten securities are still only `full_report_candidate_set` until independent membership evidence confirms the relevant set.

## Provenance requirements

Every retained evidence row must preserve provider, public URL or deterministic query URL, evidence date, raw-response SHA256 when available, and evidence role. Conflicts remain fail-closed and are recorded rather than resolved by selecting a favorable source.

## Promotion rule for historical-research eligibility

A scheduled 931152 membership node may be marked complete for historical research when either:

1. a first-party dated constituent/adjustment source establishes the node; or
2. a reproducible secondary dated set/interval reconstruction is independently cross-checked and the reconstructed membership is internally consistent with adjacent nodes.

This promotion is limited to historical research data eligibility. It does not grant production authority, open the 2024+ holdout, or change technology V1 / portfolio execution permissions.
