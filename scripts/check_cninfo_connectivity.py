from __future__ import annotations

import json

from tech_sentiment.cninfo_direct import fetch_cninfo_announcements_direct
from tech_sentiment.official_filing_facts import (
    download_official_document,
    extract_pdf_text,
    extract_standard_filing_facts,
)
from tech_sentiment.pit_public_materialization import classify_cninfo_title


PROBES = (
    {
        "symbol": "600519",
        "start_date": "2024-04-02",
        "end_date": "2024-04-04",
        "expected_title_token": "2023年年度报告",
    },
    {
        "symbol": "000538",
        "start_date": "2024-03-29",
        "end_date": "2024-03-31",
        "expected_title_token": "2023年年度报告",
    },
)


def main() -> None:
    results: list[dict[str, object]] = []
    attachment_probe: dict[str, object] | None = None
    earnings_probe: dict[str, object] | None = None
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
        immutable = matching[
            matching["公告附件链接"].astype(str).str.startswith(
                "https://static.cninfo.com.cn/"
            )
        ]
        if immutable.empty:
            raise SystemExit(
                "CNINFO connectivity/protocol probe failed: "
                f"{probe['symbol']} lacks immutable HTTPS attachment identity"
            )
        if attachment_probe is None:
            canonical_url = str(immutable.iloc[0]["公告附件链接"]).strip()
            downloaded = download_official_document(canonical_url)
            if not downloaded.content.startswith(b"%PDF-"):
                raise SystemExit(
                    "CNINFO connectivity/protocol probe failed: "
                    "official attachment did not return PDF bytes"
                )
            extracted_text = extract_pdf_text(downloaded.content)
            if len(extracted_text.strip()) < 100:
                raise SystemExit(
                    "CNINFO connectivity/protocol probe failed: "
                    "official PDF lacks a usable text layer"
                )
            facts = extract_standard_filing_facts(extracted_text)
            required_facts = {"OPERATING_REVENUE", "NET_PROFIT_PARENT", "BASIC_EPS"}
            missing_facts = required_facts - set(facts)
            if missing_facts:
                raise SystemExit(
                    "CNINFO connectivity/protocol probe failed: "
                    f"official annual report parser missing critical facts {sorted(missing_facts)}"
                )
            attachment_probe = {
                "symbol": probe["symbol"],
                "canonical_attachment_url": canonical_url,
                "retrieval_url": downloaded.retrieval_url or downloaded.url,
                "document_sha256": downloaded.sha256,
                "pdf_bytes_verified": True,
                "pdf_text_layer_verified": True,
                "critical_filing_facts_verified": sorted(required_facts),
            }
        results.append(
            {
                "symbol": probe["symbol"],
                "query_window": [probe["start_date"], probe["end_date"]],
                "matching_records": int(len(matching)),
                "protocol_ok": True,
            }
        )

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
                "earnings_probe": earnings_probe,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
