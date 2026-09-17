import json

import pandas as pd

from tech_sentiment.issuer_earnings_pit import (
    classify_explicit_issuer_earnings_direction,
    enrich_issuer_earnings_direction,
)


def test_direction_only_uses_standardized_issuer_guidance_tokens():
    assert classify_explicit_issuer_earnings_direction("2026年度业绩预告：预增") == "UP"
    assert classify_explicit_issuer_earnings_direction("2026年度业绩预告：扭亏为盈") == "UP"
    assert classify_explicit_issuer_earnings_direction("2026年度业绩预告：预减") == "DOWN"
    assert classify_explicit_issuer_earnings_direction("2026年度业绩预告：首亏") == "DOWN"
    assert classify_explicit_issuer_earnings_direction("2026年度业绩预告") == "UNKNOWN"


def test_enrichment_preserves_registered_source_and_document_identity():
    provenance = json.dumps({"source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE"})
    frame = pd.DataFrame(
        [
            {
                "evidence_id": "cninfo:1",
                "entity_id": "600000.SH",
                "evidence_type": "ISSUER_EARNINGS_FORECAST",
                "event_date": "2026-01-20",
                "evidence_available_date": "2026-01-21",
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
                "document_id": "1",
                "revision_id": "DOCUMENT:1",
                "provenance": provenance,
                "ingestion_identity": "old",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "title": "2025年度业绩预告：预减",
                "source_url_identity": "https://www.cninfo.com.cn/example",
            }
        ]
    )
    out = enrich_issuer_earnings_direction(frame)
    assert out.loc[0, "source_identity"] == "CNINFO_ANNOUNCEMENT_ARCHIVE"
    assert out.loc[0, "document_id"] == "1"
    payload = json.loads(out.loc[0, "evidence_payload"])
    assert payload["earnings_expectation_direction"] == "DOWN"
    assert payload["earnings_direction_basis"] == "EXPLICIT_ISSUER_GUIDANCE_TOKEN"
