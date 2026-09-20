from __future__ import annotations

import json

import pandas as pd

from tech_sentiment.exchange_earnings_materialization import (
    EXCHANGE_EARNINGS_SOURCES,
    materialize_registered_exchange_earnings_directions,
)


def _row(
    source: str,
    entity: str,
    document: str,
    url: str,
    title: str = "2025年度业绩预告",
) -> dict[str, object]:
    provider = (
        "SHANGHAI_STOCK_EXCHANGE"
        if source == "SSE_ANNOUNCEMENT_ARCHIVE"
        else "SHENZHEN_STOCK_EXCHANGE"
    )
    return {
        "evidence_id": f"{source}:{document}",
        "entity_id": entity,
        "evidence_type": "ISSUER_EARNINGS_FORECAST",
        "event_date": "2026-01-05",
        "evidence_available_date": "2026-01-06",
        "source_identity": source,
        "provider": provider,
        "document_id": document,
        "revision_id": f"DOCUMENT:{document}",
        "provenance": json.dumps(
            {"availability_rule": "DATE_ONLY_NEXT_TRADE_DATE"},
            ensure_ascii=False,
        ),
        "ingestion_identity": f"ingest-{document}",
        "availability_state": "HISTORICAL_RECONSTRUCTABLE",
        "title": title,
        "source_url_identity": url,
    }


def test_exchange_bridge_preserves_registered_source_and_frozen_classifier() -> None:
    frame = pd.DataFrame(
        [
            _row(
                "SSE_ANNOUNCEMENT_ARCHIVE",
                "688001.SH",
                "sse-1",
                "https://www.sse.com.cn/example.pdf",
            ),
            _row(
                "SZSE_ANNOUNCEMENT_ARCHIVE",
                "300001.SZ",
                "szse-1",
                "https://www.szse.cn/example.pdf",
            ),
        ]
    )

    texts = {
        "https://www.sse.com.cn/example.pdf": "业绩预告类型：预减",
        "https://www.szse.cn/example.pdf": "业绩预告类型：预增",
    }

    def loader(url: str) -> dict[str, str]:
        return {
            "document_url": url,
            "document_retrieval_url": url,
            "document_sha256": ("a" if "sse" in url else "b") * 64,
            "text": texts[url],
        }

    result = materialize_registered_exchange_earnings_directions(
        frame,
        document_loader=loader,
    )

    assert len(result.evidence) == 2
    assert set(result.evidence["source_identity"]) == EXCHANGE_EARNINGS_SOURCES
    directions = {}
    for _, row in result.evidence.iterrows():
        payload = json.loads(row["evidence_payload"])
        directions[str(row["entity_id"])] = payload["earnings_expectation_direction"]
        assert payload["classifier_version"] == "issuer-explicit-guidance-v1"
        assert payload["numeric_threshold_used"] is False
        assert payload["price_or_return_used"] is False
        provenance = json.loads(row["provenance"])
        assert provenance["source_identity_inherited_not_new_evidence_source"] is True
        assert provenance["source_archive_refetch"] is False
        assert provenance["future_prices_or_returns_used"] is False

    assert directions == {"688001.SH": "DOWN", "300001.SZ": "UP"}
    assert result.summary["formal_bundle_integration_performed"] is False
    assert result.summary["evidence_qualification_changed"] is False
    assert result.summary["outcomes_read"] is False


def test_unknown_exchange_document_remains_unclassified_and_emits_no_evidence() -> None:
    frame = pd.DataFrame(
        [
            _row(
                "SSE_ANNOUNCEMENT_ARCHIVE",
                "688001.SH",
                "sse-2",
                "https://www.sse.com.cn/unknown.pdf",
            )
        ]
    )

    result = materialize_registered_exchange_earnings_directions(
        frame,
        document_loader=lambda url: {
            "document_url": url,
            "document_retrieval_url": url,
            "document_sha256": "c" * 64,
            "text": "公司预计经营情况存在不确定性",
        },
    )
    assert result.evidence.empty
    assert result.errors.empty
    assert len(result.unclassified) == 1
    assert (
        result.unclassified.iloc[0]["reason"]
        == "UNCLASSIFIED_NO_EXPLICIT_UNAMBIGUOUS_DIRECTION"
    )


def test_cninfo_is_not_reclassified_by_exchange_bridge() -> None:
    frame = pd.DataFrame(
        [
            {
                **_row(
                    "SSE_ANNOUNCEMENT_ARCHIVE",
                    "688001.SH",
                    "sse-3",
                    "https://www.sse.com.cn/ignored.pdf",
                ),
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
            }
        ]
    )
    result = materialize_registered_exchange_earnings_directions(
        frame,
        document_loader=lambda url: (_ for _ in ()).throw(
            AssertionError("CNINFO must remain on the existing CNINFO materializer")
        ),
    )
    assert result.evidence.empty
    assert result.summary["status"] == "NO_ELIGIBLE_EXCHANGE_FORECAST_RECORDS"


def test_non_forecast_and_non_historical_records_are_excluded() -> None:
    base = _row(
        "SSE_ANNOUNCEMENT_ARCHIVE",
        "688001.SH",
        "sse-4",
        "https://www.sse.com.cn/excluded.pdf",
    )
    non_forecast = dict(base)
    non_forecast["evidence_type"] = "MAJOR_EVENT"
    forward_only = dict(base)
    forward_only["document_id"] = "sse-5"
    forward_only["availability_state"] = "FORWARD_ONLY"

    result = materialize_registered_exchange_earnings_directions(
        pd.DataFrame([non_forecast, forward_only]),
        document_loader=lambda url: (_ for _ in ()).throw(
            AssertionError("excluded records must not download documents")
        ),
    )
    assert result.evidence.empty
    assert result.summary["forecast_documents"] == 0
