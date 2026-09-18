import pandas as pd
import pytest

from tech_sentiment.financing_materialization import (
    _sse_margin_payload_frame,
    _szse_margin_payload_frame,
    materialize_financing_history,
    normalize_sse_financing_history,
    normalize_szse_financing_snapshot,
)


def test_sse_financing_history_is_cny_and_date_unique():
    raw = pd.DataFrame(
        {
            "信用交易日期": ["2022-01-04", "2022-01-05"],
            "融资余额": [9.1e11, 9.2e11],
        }
    )
    out = normalize_sse_financing_history(raw)
    assert list(out["sse_source_unit"].unique()) == ["CNY"]
    assert out.loc[0, "sse_financing_balance"] == pytest.approx(9.1e11)


def test_szse_snapshot_keeps_frozen_cny_100m_source_unit():
    raw = pd.DataFrame({"融资余额": [8000.0]})
    row = normalize_szse_financing_snapshot(raw, observation_date="2022-01-04")
    assert row["szse_source_unit"] == "CNY_100M"
    assert row["szse_financing_balance"] == pytest.approx(8000.0)




def test_sse_margin_named_payload_preserves_cny_balance():
    frame = _sse_margin_payload_frame(
        {
            "pageHelp": {
                "data": [
                    {"opDate": "20220104", "rzye": "910000000000.00"},
                    {"opDate": "20220105", "rzye": "920000000000.00"},
                ]
            }
        }
    )
    out = normalize_sse_financing_history(frame)
    assert out["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2022-01-04",
        "2022-01-05",
    ]
    assert out["sse_financing_balance"].tolist() == [
        910_000_000_000.0,
        920_000_000_000.0,
    ]


def test_szse_margin_named_payload_keeps_100m_yuan_raw_unit():
    frame = _szse_margin_payload_frame(
        [{"data": [{"jrrzye": "8,000.50"}]}]
    )
    row = normalize_szse_financing_snapshot(
        frame, observation_date="2022-01-04"
    )
    assert row["szse_source_unit"] == "CNY_100M"
    assert row["szse_financing_balance"] == pytest.approx(8000.5)

def test_materializer_preserves_missing_dates_and_never_guesses_units():
    cal = pd.to_datetime(["2022-01-04", "2022-01-05", "2022-01-06"])

    def sse_fetcher(start: str, end: str) -> pd.DataFrame:
        assert start == "20220104"
        assert end == "20220106"
        return pd.DataFrame(
            {
                "信用交易日期": ["2022-01-04", "2022-01-05", "2022-01-06"],
                "融资余额": [9.0e11, 9.1e11, 9.2e11],
            }
        )

    def szse_fetcher(date: str) -> pd.DataFrame:
        if date == "20220105":
            raise RuntimeError("source unavailable")
        value = {"20220104": 8000.0, "20220106": 8100.0}[date]
        return pd.DataFrame({"融资余额": [value]})

    result = materialize_financing_history(
        cal, sse_fetcher=sse_fetcher, szse_fetcher=szse_fetcher
    )
    assert len(result.raw) == 3
    assert result.raw["bilateral_complete"].tolist() == [True, False, True]
    assert result.summary["bilateral_complete_days"] == 2
    assert result.summary["bilateral_coverage"] == pytest.approx(2 / 3)
    assert result.summary["role"] == "RESEARCH_INPUT"
    assert result.summary["included_in_capital_regime_composite"] is False
    assert result.summary["no_unit_inference_from_anomaly"] is True
    assert result.canonical.loc[0, "szse_financing_balance_yuan"] == pytest.approx(
        8000.0 * 1e8
    )
    assert len(result.errors) == 1


def test_financing_retries_transient_szse_failure():
    calls = 0

    def sse_fetcher(start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame({
            "信用交易日期": ["2022-01-04"],
            "融资余额": [9.0e11],
        })

    def szse_fetcher(date: str) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(104, "reset")
        return pd.DataFrame({"融资余额": [8000.0]})

    result = materialize_financing_history(
        pd.to_datetime(["2022-01-04"]),
        sse_fetcher=sse_fetcher,
        szse_fetcher=szse_fetcher,
        retry_backoff_seconds=0,
    )
    assert calls == 2
    assert result.errors.empty
    assert result.summary["bilateral_coverage"] == pytest.approx(1.0)
