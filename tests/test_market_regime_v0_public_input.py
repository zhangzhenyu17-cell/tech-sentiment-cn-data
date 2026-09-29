from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.index_price import (
    _DIRECT_EASTMONEY_SECIDS,
    _TENCENT_INDEX_SYMBOLS,
)
_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "market_regime_v0_public_materializer",
    _ROOT / "scripts/materialize_market_regime_v0_public_input.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
INDEX_CODES = _MODULE.INDEX_CODES
build_market_regime_public_input = _MODULE.build_market_regime_public_input


def _fake_fetcher(code: str, *, start_date: str, end_date: str, **_: object) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=130)
    base = {"000985": 1000, "000300": 900, "000905": 800, "399006": 700}[code]
    close = pd.Series(range(base, base + len(dates)), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "index_code": code,
            "open": close - 1,
            "close": close,
            "high": close + 1,
            "low": close - 2,
            "volume": 1.0,
            "amount": 2.0,
            "pct_chg": 0.1,
            "provider": "fixture",
        }
    )


def test_formal_market_regime_indexes_have_reliable_provider_routes() -> None:
    for code in INDEX_CODES:
        assert code in _TENCENT_INDEX_SYMBOLS
        assert code in _DIRECT_EASTMONEY_SECIDS


def test_public_materializer_is_raw_only_and_aligned(tmp_path: Path) -> None:
    csv_path = tmp_path / "input.csv"
    manifest_path = tmp_path / "manifest.json"
    manifest = build_market_regime_public_input(
        start_date="2026-01-01",
        as_of_date="2026-12-31",
        output_csv=csv_path,
        output_manifest=manifest_path,
        fetcher=_fake_fetcher,
    )
    assert manifest["contains_model_output"] is False
    assert manifest["contains_private_evidence"] is False
    assert manifest["public_handoff_ready_grants_private_qualification"] is False
    assert manifest["automatic_trigger"] is False
    assert manifest["index_codes"] == list(INDEX_CODES)
    assert manifest["row_count"] == 520
    saved = json.loads(manifest_path.read_text())
    assert saved == manifest


def test_public_materializer_fails_closed_on_short_history(tmp_path: Path) -> None:
    def short(code: str, **kwargs: object) -> pd.DataFrame:
        return _fake_fetcher(code, **kwargs).iloc[:120].copy()

    with pytest.raises(ValueError, match="insufficient history"):
        build_market_regime_public_input(
            start_date="2026-01-01",
            as_of_date="2026-12-31",
            output_csv=tmp_path / "x.csv",
            output_manifest=tmp_path / "x.json",
            fetcher=short,
        )


def test_committed_current_public_input_matches_manifest() -> None:
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data/reference/market_regime_v0_public_input_latest.csv"
    manifest_path = root / "reference/market_regime_v0_public_input_latest.json"
    frame = pd.read_csv(csv_path, dtype={"index_code": str})
    manifest = json.loads(manifest_path.read_text())
    import hashlib

    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == manifest["csv_sha256"]
    assert set(frame["index_code"].str.zfill(6)) == set(INDEX_CODES)
    assert manifest["latest_market_date"] == "2026-09-29"
    assert manifest["row_count"] == len(frame) == 812
    assert all(manifest["rows_by_index"][code] >= 121 for code in INDEX_CODES)
    assert manifest["contains_model_output"] is False
