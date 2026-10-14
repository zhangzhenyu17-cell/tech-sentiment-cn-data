import pandas as pd

from tech_sentiment.prospective_source_observation_v3 import classify_source_incomplete_v3


def test_official_http_403_transport_exhaustion_is_transport_failure() -> None:
    data = pd.DataFrame(columns=["date", "fund_code"])
    errors = pd.DataFrame(
        [{"date": "2026-10-14", "error": "SSE ETF official HTTPS transports exhausted; plain-common:HTTPError:403 Client Error: Forbidden"}]
    )
    assert classify_source_incomplete_v3(data, errors) == "TRANSPORT_FAILURE"


def test_schema_error_stays_review_required() -> None:
    data = pd.DataFrame(columns=["date", "fund_code"])
    errors = pd.DataFrame(
        [{"date": "2026-10-14", "error": "unexpected provider schema"}]
    )
    assert classify_source_incomplete_v3(data, errors) == "SOURCE_DATA_ERROR_REQUIRES_REVIEW"
