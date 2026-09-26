# Innovation Drug CDE/NMPA Snapshot Adapter V1

## Purpose

This adapter consumes a manually captured or officially exported public CDE/NMPA snapshot and converts exact-mapped rows into outcome-blind Innovation Drug raw event identities.

It does **not** scrape around CDE anti-bot controls, infer missing publication dates, fuzzy-match applicants, classify direction, assign weights, compute a sector score, read outcomes, or change evidence/Production/trading authority.

Canonical source identity:

`NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE`

## Registered official source pages

| Family | Official page | Frozen row categories | Record-level date rule |
| --- | --- | --- | --- |
| Priority review | `https://www.cde.org.cn/main/xxgk/listpage/2f78f372d351c6851af7431c7710a731` | 拟优先审评品种 / 纳入优先审评品种名单 | Page exposes 公示日期; use `PUBLICATION_DATE_EXPLICIT` |
| Breakthrough therapy | `https://www.cde.org.cn/main/xxgk/listpage/da6efd086c099b7fc949121166f0130c` | 拟突破性治疗品种 / 纳入突破性治疗品种名单 | Page exposes 公示日期; use `PUBLICATION_DATE_EXPLICIT` |
| Implied clinical-trial permission | `https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c` | 临床试验默示许可 | Public table does not expose a record-level publication date; use `FIRST_OBSERVED_SNAPSHOT_DATE` only |
| Conditional approval | `https://www.cde.org.cn/main/xxgk/listpage/c8d79e513a6df98adf05893281ace198` | 附条件批准品种 | Use the explicit approval/public date captured from the official record; no inferred date |

## Snapshot row schema

Required identity fields:

- `entity_id`
- `entity_mapping_basis`
- `category`
- `record_id`
- `applicant`
- `drug_name`
- `indication`
- `source_url`
- `availability_basis`

Allowed exact entity mapping bases:

- `EXACT_APPLICANT_ALIAS_REGISTRY`
- `EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE`

No fuzzy company-name matching is allowed.

### Explicit-date records

Use:

`availability_basis=PUBLICATION_DATE_EXPLICIT`

and provide `publication_date`.

The date-only official publication date is delayed to the next real A-share trading date before becoming market-available.

### First-observed records

Use:

`availability_basis=FIRST_OBSERVED_SNAPSHOT_DATE`

and provide `snapshot_captured_at`.

`publication_date` must be empty. The capture timestamp is converted to Asia/Shanghai local date and delayed to the next real A-share trading date. These rows are emitted as:

`PROSPECTIVE_FIRST_OBSERVED_ONLY`

They may **not** be used to reconstruct earlier historical availability.

## Capture manifest

The manifest must identify:

- `manifest_id=INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_CAPTURE_V1`
- unique `snapshot_id`
- `source_identity=NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE`
- timezone-bearing `captured_at`
- capture method: `OFFICIAL_EXPORT` or `BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE`
- registered `source_urls`
- SHA256 of the exact applicant/entity mapping registry used

All safety flags must remain false: outcome reads, fuzzy mapping, publication-date inference, historical backfill from current page presence, current-constituent backfill, direction classification, predictive weights, sector scoring, and evidence qualification change.

## Materialization

Example:

```bash
python scripts/materialize_innovation_drug_cde_nmpa_snapshot_v1.py \
  --snapshot-csv /path/to/mapped_snapshot.csv \
  --snapshot-manifest-json /path/to/snapshot_manifest.json \
  --trading-calendar-csv /path/to/trading_calendar.csv \
  --output-dir /path/to/output
```

Outputs:

- `cde_nmpa_mapped_raw_events.csv`
- `cde_nmpa_snapshot_summary.json`

The adapter remains fail-closed until a real official snapshot and exact entity mapping are supplied. Adapter readiness does not change the formal sector KPI state from `DATA_INSUFFICIENT`.
