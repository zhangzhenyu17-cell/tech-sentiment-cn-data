from __future__ import annotations

import json

from tech_sentiment.cninfo_direct import fetch_cninfo_announcements_direct


PROBES = (
    {
        "symbol": "600519",
        "start_date": "2024-04-02",
        "end_date": "2024-04-04",
        "expected_title_token": "2023年年度报告",
    },
    {
        "symbol": "000538",
        "start_date": "2024-04-10",
        "end_date": "2024-04-12",
        "expected_title_token": "2023年年度报告",
    },
)


def main() -> None:
    results: list[dict[str, object]] = []
    for probe in PROBES:
        frame = fetch_cninfo_announcements_direct(
            symbol=str(probe["symbol"]),
            start_date=str(probe["start_date"]),
            end_date=str(probe["end_date"]),
        )
        matching = frame[
            frame["公告标题"].astype(str).str.contains(
                str(probe["expected_title_token"]), regex=False
            )
        ]
        if matching.empty:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"{probe['symbol']} missing expected historical disclosure"
            )
        if not matching["公告附件链接"].astype(str).str.startswith(
            "https://static.cninfo.com.cn/"
        ).any():
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"{probe['symbol']} lacks immutable HTTPS attachment identity"
            )
        results.append(
            {
                "symbol": probe["symbol"],
                "query_window": [probe["start_date"], probe["end_date"]],
                "matching_records": int(len(matching)),
                "protocol_ok": True,
            }
        )
    print(
        json.dumps(
            {
                "status": "CNINFO_PROTOCOL_CONNECTIVITY_OK",
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
