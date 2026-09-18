from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pandas as pd

from tech_sentiment.bounded_retry import call_with_bounded_network_retry
from tech_sentiment.cninfo_direct import fetch_cninfo_announcements_direct
from tech_sentiment.data_akshare import fetch_stock_history
from tech_sentiment.filing_materialization import is_numeric_financial_filing_title
from tech_sentiment.fundamental_pit_state import (
    REQUIRED_FACTS,
    materialize_fundamental_state_evidence,
)
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.official_filing_facts import (
    build_filing_fact_rows,
    download_official_document,
    extract_pdf_text,
    extract_standard_filing_facts,
)
from tech_sentiment.pit_public_materialization import classify_cninfo_title
from tech_sentiment.trailing_valuation_pit import (
    build_trailing_valuation_rail,
    valuation_rail_to_pit_evidence,
)


PROBES = (
    {
        "role": "600519_CURRENT",
        "symbol": "600519",
        "start_date": "2024-04-02",
        "end_date": "2024-04-04",
        "expected_title_token": "2023年年度报告",
    },
    {
        "role": "000538_CURRENT",
        "symbol": "000538",
        "start_date": "2024-03-29",
        "end_date": "2024-03-31",
        "expected_title_token": "2023年年度报告",
    },
)

COMPARABLE_PROBE = {
    "role": "600519_PRIOR",
    "symbol": "600519",
    "start_date": "2023-03-01",
    "end_date": "2023-05-15",
    "expected_title_token": "2022年年度报告",
}


def _entity_id(symbol: str) -> str:
    code = str(symbol).zfill(6)
    suffix = ".SH" if code.startswith(("5", "6", "9")) else ".SZ"
    return f"{code}{suffix}"


def _select_numeric_report_candidate(
    frame: pd.DataFrame,
    *,
    expected_title_token: str,
) -> tuple[pd.Series, int, int]:
    """Select one deterministic Chinese numeric financial report for the probe.

    The preflight must exercise the same title eligibility contract as the formal
    filing materializer. English translations, summaries, audit reports, inquiry
    replies and similar documents are excluded by is_numeric_financial_filing_title.

    When multiple eligible versions exist in the probe window, choose the latest
    official publication timestamp. If more than one distinct document remains
    at that exact latest timestamp, fail closed rather than relying on source row
    order.
    """

    required = {"公告标题", "公告时间", "公告链接", "公告附件链接"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"CNINFO probe frame missing required columns: {sorted(missing)}"
        )

    titles = frame["公告标题"].astype(str)
    raw_matches = frame[
        titles.str.contains(expected_title_token, regex=False)
    ].copy()
    eligible = raw_matches[
        raw_matches["公告标题"].map(is_numeric_financial_filing_title)
    ].copy()
    eligible = eligible[
        eligible["公告附件链接"].astype(str).str.startswith(
            "https://static.cninfo.com.cn/"
        )
    ].copy()

    if eligible.empty:
        raise ValueError(
            "no eligible numeric Chinese financial report with immutable HTTPS attachment"
        )

    eligible["_publication_order"] = pd.to_datetime(
        eligible["公告时间"], errors="raise"
    )
    latest_publication = eligible["_publication_order"].max()
    latest = eligible[
        eligible["_publication_order"].eq(latest_publication)
    ].copy()

    if len(latest) != 1:
        identities: list[str] = []
        for _, row in latest.iterrows():
            try:
                document_id = _announcement_id(str(row["公告链接"]))
            except Exception:
                document_id = "UNKNOWN"
            identities.append(
                f"{document_id}:{str(row['公告标题']).strip()}"
            )
        raise ValueError(
            "ambiguous latest eligible financial reports: "
            + " | ".join(sorted(identities))
        )

    return (
        latest.iloc[0].drop(labels=["_publication_order"]),
        int(len(raw_matches)),
        int(len(eligible)),
    )


def _announcement_id(detail_url: str) -> str:
    values = parse_qs(urlparse(str(detail_url)).query).get("announcementId", [])
    document_id = str(values[0]).strip() if values else ""
    if not document_id:
        raise ValueError("CNINFO detail URL lacks immutable announcementId")
    return document_id


def main() -> None:
    results: list[dict[str, object]] = []
    attachment_probe: dict[str, object] | None = None
    annual_report_fact_probes: list[dict[str, object]] = []
    earnings_probe: dict[str, object] | None = None
    fact_frames: dict[str, pd.DataFrame] = {}
    for probe in (*PROBES, COMPARABLE_PROBE):
        frame = fetch_cninfo_announcements_direct(
            symbol=str(probe["symbol"]),
            start_date=str(probe["start_date"]),
            end_date=str(probe["end_date"]),
        )
        try:
            selected_row, raw_match_count, eligible_match_count = (
                _select_numeric_report_candidate(
                    frame,
                    expected_title_token=str(probe["expected_title_token"]),
                )
            )
        except Exception as exc:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"role={probe['role']} symbol={probe['symbol']} "
                f"candidate_selection_error={type(exc).__name__}: {exc}"
            ) from exc
        selected_title = str(selected_row["公告标题"]).strip()
        canonical_url = str(selected_row["公告附件链接"]).strip()
        downloaded = download_official_document(canonical_url)
        if not downloaded.content.startswith(b"%PDF-"):
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"{probe['symbol']} official attachment did not return PDF bytes"
            )
        extracted_text = extract_pdf_text(downloaded.content)
        if len(extracted_text.strip()) < 100:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"{probe['symbol']} official PDF lacks a usable text layer"
            )
        try:
            facts = extract_standard_filing_facts(extracted_text)
        except Exception as exc:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"role={probe['role']} symbol={probe['symbol']} "
                f"title={selected_title!r} raw_title_matches={raw_match_count} "
                f"eligible_matches={eligible_match_count} "
                f"canonical_url={canonical_url} "
                f"retrieval_url={downloaded.retrieval_url or downloaded.url} "
                f"sha256={downloaded.sha256} parser_error="
                f"{type(exc).__name__}: {exc}"
            ) from exc
        required_facts = set(REQUIRED_FACTS) | {"BASIC_EPS"}
        missing_facts = required_facts - set(facts)
        if missing_facts:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"role={probe['role']} symbol={probe['symbol']} "
                "annual report parser missing critical facts "
                f"{sorted(missing_facts)}"
            )
        row = selected_row
        title = selected_title
        publication = str(row["公告时间"]).strip()
        document_id = _announcement_id(str(row["公告链接"]))
        fact_rows = build_filing_fact_rows(
            entity_id=_entity_id(str(probe["symbol"])),
            title=title,
            evidence_available_date=pd.Timestamp(publication).normalize(),
            publication_timestamp=publication,
            source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
            provider="CNINFO",
            document_id=document_id,
            revision_id=f"DOCUMENT:{document_id}:SHA256:{downloaded.sha256}",
            document_url=canonical_url,
            document_sha256=downloaded.sha256,
            text=extracted_text,
        )
        fact_frames[str(probe["role"])] = fact_rows
        parsed_probe = {
            "role": probe["role"],
            "symbol": probe["symbol"],
            "title": title,
            "document_id": document_id,
            "canonical_attachment_url": canonical_url,
            "retrieval_url": downloaded.retrieval_url or downloaded.url,
            "document_sha256": downloaded.sha256,
            "pdf_bytes_verified": True,
            "pdf_text_layer_verified": True,
            "critical_filing_facts_verified": sorted(required_facts),
        }
        annual_report_fact_probes.append(parsed_probe)
        if attachment_probe is None:
            attachment_probe = dict(parsed_probe)
        results.append(
            {
                "symbol": probe["symbol"],
                "query_window": [probe["start_date"], probe["end_date"]],
                "raw_title_matches": raw_match_count,
                "eligible_matches": eligible_match_count,
                "protocol_ok": True,
            }
        )

    comparable_facts = pd.concat(
        [
            fact_frames["600519_PRIOR"],
            fact_frames["600519_CURRENT"],
        ],
        ignore_index=True,
        sort=False,
    )
    fundamental = materialize_fundamental_state_evidence(
        comparable_facts,
        target_start_date="2024-01-01",
        target_end_date="2024-12-31",
    )
    if (
        fundamental.summary.get("readiness_state") != "QUALIFIED_INPUT"
        or fundamental.summary.get("latest_required_comparable_coverage_complete") is not True
        or fundamental.evidence.empty
        or not fundamental.evidence["availability_state"].astype(str).eq(
            "HISTORICAL_RECONSTRUCTABLE"
        ).any()
    ):
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            "real annual-report comparable facts do not reach frozen fundamental readiness; "
            f"summary={fundamental.summary}"
        )
    fundamental_probe = {
        "entity_id": "600519.SH",
        "readiness_state": fundamental.summary.get("readiness_state"),
        "qualified_state_records": int(
            fundamental.summary.get("qualified_state_records") or 0
        ),
        "latest_required_comparable_coverage_complete": bool(
            fundamental.summary.get("latest_required_comparable_coverage_complete")
        ),
        "contract_id": fundamental.summary.get("contract_id"),
    }

    valuation_start = "2024-04-01"
    valuation_end = "2024-05-31"
    calendar_frame = fetch_index_history(
        "000688",
        start_date=valuation_start,
        end_date=valuation_end,
    )
    if calendar_frame.empty or "date" not in calendar_frame.columns:
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            "valuation smoke trading calendar is unavailable"
        )
    trading_dates = pd.to_datetime(
        calendar_frame["date"], errors="raise"
    ).dt.normalize()

    price = pd.DataFrame()
    price_errors: list[str] = []
    price_provider = ""
    for provider in ("tencent", "eastmoney"):
        try:
            candidate = call_with_bounded_network_retry(
                lambda p=provider: fetch_stock_history(
                    "600519",
                    start_date=valuation_start,
                    end_date=valuation_end,
                    adjust="",
                    provider=p,
                ),
                attempts=3,
                backoff_seconds=0.5,
            )
            if candidate is not None and len(candidate):
                price = candidate
                price_provider = provider
                break
            price_errors.append(f"{provider}:empty")
        except Exception as exc:
            price_errors.append(f"{provider}:{type(exc).__name__}:{exc}")
    if price.empty:
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            f"valuation smoke price history unavailable; errors={price_errors}"
        )

    valuation_rail = build_trailing_valuation_rail(
        filing_facts=comparable_facts,
        stock_prices=price,
        trading_dates=trading_dates,
    )
    valuation_evidence = valuation_rail_to_pit_evidence(valuation_rail)
    if valuation_evidence.empty:
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            "real annual-report + price inputs produced no trailing valuation evidence"
        )
    valuation_probe = {
        "entity_id": "600519.SH",
        "price_provider": price_provider,
        "price_rows": int(len(price)),
        "calendar_rows": int(len(trading_dates)),
        "valuation_rail_rows": int(len(valuation_rail)),
        "valuation_evidence_rows": int(len(valuation_evidence)),
        "real_input_integration_verified": True,
    }

    earnings_frame = fetch_cninfo_announcements_direct(
        symbol="000538",
        start_date="20230713",
        end_date="20230714",
    )
    earnings_candidates = earnings_frame[
        earnings_frame["公告标题"].map(classify_cninfo_title).eq("ISSUER_EARNINGS_FORECAST")
    ]
    if earnings_candidates.empty:
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            "000538 historical earnings forecast disclosure is unreachable"
        )
    earnings_row = earnings_candidates.iloc[0]
    earnings_url = str(earnings_row["公告附件链接"]).strip()
    earnings_document = download_official_document(earnings_url)
    earnings_text = extract_pdf_text(earnings_document.content)
    if "业绩预告" not in (str(earnings_row["公告标题"]) + earnings_text):
        raise SystemExit(
            "CNINFO connectivity/protocol probe failed: "
            "earnings forecast PDF text does not preserve disclosure identity"
        )
    earnings_probe = {
        "symbol": "000538",
        "query_window": ["2023-07-13", "2023-07-14"],
        "title": str(earnings_row["公告标题"]),
        "canonical_attachment_url": earnings_url,
        "retrieval_url": earnings_document.retrieval_url or earnings_document.url,
        "document_sha256": earnings_document.sha256,
        "pdf_text_layer_verified": True,
        "earnings_document_path_verified": True,
    }

    print(
        json.dumps(
            {
                "status": "CNINFO_PROTOCOL_CONNECTIVITY_OK",
                "diagnostic_only": True,
                "canonical_evidence_output": False,
                "forward_outcome_read": False,
                "parameter_search": False,
                "probes": results,
                "attachment_probe": attachment_probe,
                "annual_report_fact_probes": annual_report_fact_probes,
                "fundamental_mapping_probe": fundamental_probe,
                "valuation_probe": valuation_probe,
                "earnings_probe": earnings_probe,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
