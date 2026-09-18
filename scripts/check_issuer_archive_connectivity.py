from __future__ import annotations

import json

from tech_sentiment.official_pit_archives import (
    fetch_sse_announcements,
    fetch_szse_announcements,
)


PROBES = (
    {
        "market": "SSE",
        "symbol": "600519",
        "start_date": "2024-04-01",
        "end_date": "2024-04-05",
        "expected_title_token": "2023年年度报告",
    },
    {
        "market": "SZSE",
        "symbol": "000538",
        "start_date": "2024-03-29",
        "end_date": "2024-03-31",
        "expected_title_token": "2023年年度报告",
    },
)


def _run_probe(probe: dict[str, str]) -> dict[str, object]:
    market = probe["market"]
    fetcher = fetch_sse_announcements if market == "SSE" else fetch_szse_announcements
    frame = call_with_bounded_network_retry(
        lambda: fetcher(
            symbol=probe["symbol"],
            start_date=probe["start_date"],
            end_date=probe["end_date"],
        ),
        attempts=3,
        backoff_seconds=0.5,
    )
    required = {"symbol", "title", "publication_time", "document_id", "source_url"}
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(
            f"{market} issuer protocol probe failed: missing columns {sorted(missing)}"
        )
    matching = frame[
        frame["title"].astype(str).str.contains(
            probe["expected_title_token"], regex=False
        )
    ]
    if matching.empty:
        raise SystemExit(
            f"{market} issuer protocol probe failed: {probe['symbol']} "
            f"missing expected historical disclosure"
        )
    row = matching.iloc[0]
    if not str(row["document_id"]).strip() or not str(row["source_url"]).startswith("https://"):
        raise SystemExit(
            f"{market} issuer protocol probe failed: canonical document identity incomplete"
        )
    return {
        "market": market,
        "symbol": probe["symbol"],
        "query_window": [probe["start_date"], probe["end_date"]],
        "matching_records": int(len(matching)),
        "document_id_present": True,
        "official_https_url_present": True,
        "protocol_ok": True,
    }


def main() -> None:
    results = [_run_probe(dict(probe)) for probe in PROBES]
    print(
        json.dumps(
            {
                "status": "ISSUER_ARCHIVE_PROTOCOL_CONNECTIVITY_OK",
                "diagnostic_only": True,
                "canonical_evidence_output": False,
                "forward_outcome_read": False,
                "parameter_search": False,
                "probes": results,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
