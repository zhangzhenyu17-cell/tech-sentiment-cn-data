# Sector public-data eligibility gate

This public-data contract supports sector-adapter research without exposing model rules, thresholds, outcomes, portfolio logic, or private evidence.

It governs only two public-input questions:

1. Can historical index membership be reconstructed point in time from dated evidence rather than today's constituents?
2. Can each active stock-day's price-limit semantics be used without silently assuming a board-wide historical default?

## Membership evidence

`audit_membership_evidence()` is outcome-free and fail-closed. Every expected evidence period must have one manifest row. A row is complete only when it records:

- the exact effective date;
- `changed` or `no_change`;
- an admissible evidence type;
- a durable `http(s)` source URL.

A missing notice or an unsuccessful search is not evidence of `no_change`.

For the first sector, CSI Brand Name Drug Industry Index (`931152`), the design-period registry is:

`data/reference/sector_931152_membership_evidence.csv`

The registered chain contains **11 required evidence periods**: the 2019-04 launch anchor plus June/December scheduled rebalances from 2019 through 2023. They remain `pending` until dated membership evidence is captured and reviewed. This file is therefore an evidence-gap registry, not a reconstructed constituent history.

### Evidence hierarchy

Evidence is ranked by provenance, but non-official evidence is not rejected merely because it is non-official.

- **Primary evidence**: official CSI adjustment/effective-sample files, exchange or fund-company dated ETF PCF baskets, and equivalent first-party dated constituent material.
- **Secondary evidence**: reproducible historical data from established market-data publishers such as EastMoney / Tiantian Fund or Sina, provided the exact dated rows can be retained with a source URL and content hash.
- **Cross-check only**: current constituents, quarterly top-ten fund holdings, search snippets, news summaries, or a single stock's membership page when used alone.

A secondary reconstruction may satisfy a missing historical period when all of the following hold:

1. one source supplies a dated candidate universe broad enough to contain the expected index set;
2. the fund/index relationship is independently documented for the relevant period when a tracking fund is used;
3. a different source supplies dated index-membership or add/remove evidence for the individual candidates;
4. the resulting set passes internal consistency checks and unexplained differences remain fail-closed;
5. the exact source responses are retained with hashes so the reconstruction is reproducible.

This allows research to proceed when official historical downloads are unavailable while preserving a clear distinction between source grades. Evidence grade does not change model logic or grant production authority.

Useful source classes include official index-provider adjustment/effective-sample material and dated ETF creation/redemption baskets when the ETF is documented to track the same index. The existence of a tracking ETF or a PCF publication rule does not by itself prove any historical membership date; the dated source must still be captured.

### Official CSI adjustment collector

`src/tech_sentiment/csi_adjustment_evidence.py` and the one-shot script
`scripts/research/probe_csi_931152_adjustments.py` provide a reproducible, outcome-free path for locating official CSI rebalance announcements and extracting rows for index `931152` from their Excel attachments.

The collector follows the public CSI announcement APIs used by established open-source index collectors:

- `announcement/queryAnnouncementByVo` to locate notices;
- `announcement/queryAnnouncementById` to retrieve notice details and `enclosureList` attachment URLs.

The script stores source URLs, raw attachment hashes and candidate add/remove rows. It deliberately **does not edit the membership manifest** and labels its output `CANDIDATE_MEMBERSHIP_EVIDENCE_ONLY`.

An attachment is not automatically qualifying evidence merely because it was downloaded. Before a manifest period can become `complete`, the research process must still confirm the effective date, confirm that the attachment actually contains `931152` rows or otherwise proves a no-change state, and reconcile the resulting membership chain against an independent dated anchor. Absence of `931152` rows in an adjustment workbook is not, by itself, proof of `no_change`.

Excel parsing is research-only and available through:

```bash
python -m pip install -e ".[sector-data]"
python scripts/research/probe_csi_931152_adjustments.py --output-dir /tmp/931152-csi-evidence
```

### EastMoney / Tiantian Fund candidate-pool collector

`src/tech_sentiment/eastmoney_fund_holdings_evidence.py` and
`scripts/research/probe_eastmoney_159992_holdings.py` provide a reproducible secondary-data path for ETF `159992`, which tracks `931152`.

The adapter uses EastMoney / Tiantian Fund's historical holdings endpoint and stores the raw response hash. It deliberately distinguishes:

- Q2/Q4 report batches with more than ten distinct stock codes: `eastmoney_tiantian_full_fund_holdings_candidate_set`;
- Q1/Q3 or ten-stock-only disclosures: `eastmoney_tiantian_partial_holdings_crosscheck_only`.

**Neither label is an index anchor.** Real probe data showed that full fund reports can contain substantially more securities than the tracked index universe because an ETF can hold IPO allocations, substitutions and other non-index positions. Therefore the fund report is used only as a dated candidate pool / coverage cross-check. It must be filtered and validated against independent dated index-membership evidence before any formal reconstruction can qualify.

Run the outcome-free probe with:

```bash
python scripts/research/probe_eastmoney_159992_holdings.py \
  --output-dir /tmp/931152-eastmoney-evidence \
  --start-year 2020 --end-year 2023
```

Reference pages used to establish the public-data source path include:

- CSI 931152 factsheet: https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/931152factsheet.pdf
- Shenzhen Stock Exchange notice for ETF 159992 listing and start of creation/redemption on 2020-04-10: https://www.szse.cn/disclosure/notice/general/t20200407_576104.html
- Tiantian Fund 159992 holdings page: https://fundf10.eastmoney.com/ccmx_159992.html

## Stock-day price-limit semantics

`audit_daily_limit_rule_coverage()` requires a genuine point-in-time universe and explicit stock-day fields:

- `limit_pct`: applicable daily price-limit percentage when usable;
- `limit_eligible`: whether that row has a usable price-limit rule for normalized extreme-move features;
- `limit_rule_source`: provenance or an explicit reason why the rule is not eligible.

The audit deliberately does **not** infer historical ST status or special trading-day rules. A row may be retained for ordinary price/breadth calculations while being marked ineligible for price-limit-normalized extreme features.

The caller supplies `min_daily_coverage`. This public module does not define the research threshold; it only measures whether the provided threshold is satisfied. Threshold selection belongs in the private preregistered research layer.

## Separation from existing technology data

The current technology pipeline is unchanged. In particular, this gate does not rewrite existing historical files, model features, provider order, adjustment mode, or production semantics. Sector inputs must use their own explicit public-data path rather than mutating technology evidence.

## Required checks

All public-repository changes continue to require:

```bash
python scripts/audit_public_tree.py
pytest -q
```

No market outcome, signal, model verdict, or private-repository material belongs in this repository.
