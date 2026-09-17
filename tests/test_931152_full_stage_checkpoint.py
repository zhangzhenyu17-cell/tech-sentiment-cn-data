from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "run_931152_oos_inputs_resumable.py"
SPEC = importlib.util.spec_from_file_location("run_931152_oos_inputs_resumable_full", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@dataclass
class _Batch:
    response_sha256: str
    source_url: str


def test_holdings_checkpoint_reuses_verified_raw_text(tmp_path, monkeypatch) -> None:
    calls = 0
    source_url = MODULE.holdings.holdings_url("159992", 2026, topline=100)
    batch = _Batch(response_sha256="a" * 64, source_url=source_url)

    def network_fetcher(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [batch], (2026, 2025), "raw holdings payload"

    monkeypatch.setattr(MODULE.holdings, "parse_eastmoney_envelope", lambda text: ("html", (2026, 2025)))
    monkeypatch.setattr(
        MODULE.holdings,
        "parse_holdings_html",
        lambda html, **kwargs: [_Batch(response_sha256=kwargs["response_sha256"], source_url=kwargs["source_url"])],
    )
    fetcher = MODULE._checkpointed_holdings_fetcher(tmp_path, network_fetcher)
    first = fetcher("159992", 2026, topline=100, timeout=5)
    assert calls == 1
    assert first[2] == "raw holdings payload"

    second = fetcher("159992", 2026, topline=100, timeout=5)
    assert calls == 1
    assert second[2] == "raw holdings payload"
    assert second[0][0].response_sha256 == "a" * 64


def _price_rows(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "symbol": [symbol, symbol],
            "open": [10.0, 10.1],
            "close": [10.1, 10.2],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "pct_chg": [1.0, 0.99],
            "amount": [1000.0, 1100.0],
            "board": ["main", "main"],
            "provider": ["eastmoney", "eastmoney"],
        }
    )


def test_stock_checkpoint_only_refetches_missing_symbols(tmp_path) -> None:
    universe = pd.DataFrame({"symbol": ["600001", "600002"]})
    calls: list[list[str]] = []

    def first_network(subset, **kwargs):
        requested = sorted(set(subset["symbol"].astype(str).str.zfill(6)))
        calls.append(requested)
        return MODULE.data_akshare.DownloadResult(
            prices=_price_rows("600001"),
            errors=pd.DataFrame([{"symbol": "600002", "error": "temporary"}]),
        )

    first = MODULE._checkpointed_stock_downloader(tmp_path, first_network)
    result1 = first(
        universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert set(result1.prices["symbol"]) == {"600001"}
    assert calls == [["600001", "600002"]]

    def second_network(subset, **kwargs):
        requested = sorted(set(subset["symbol"].astype(str).str.zfill(6)))
        calls.append(requested)
        return MODULE.data_akshare.DownloadResult(
            prices=_price_rows("600002"),
            errors=pd.DataFrame(columns=["symbol", "error"]),
        )

    second = MODULE._checkpointed_stock_downloader(tmp_path, second_network)
    result2 = second(
        universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert set(result2.prices["symbol"]) == {"600001", "600002"}
    assert calls[-1] == ["600002"]


def test_empty_stock_checkpoint_remains_retryable_without_keyerror(tmp_path) -> None:
    universe = pd.DataFrame({"symbol": ["600001"]})
    calls = 0

    def network(subset, **kwargs):
        nonlocal calls
        calls += 1
        return MODULE.data_akshare.DownloadResult(
            prices=MODULE._empty_stock_prices(),
            errors=pd.DataFrame([{"symbol": "600001", "error": "temporary"}]),
        )

    downloader = MODULE._checkpointed_stock_downloader(tmp_path, network)
    first = downloader(
        universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert list(first.prices.columns) == list(MODULE._empty_stock_prices().columns)
    assert first.prices.empty

    second = downloader(
        universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert second.prices.empty
    assert calls == 2


def test_stock_checkpoint_is_not_reused_for_different_requested_universe(tmp_path) -> None:
    first_universe = pd.DataFrame({"symbol": ["600001"]})
    second_universe = pd.DataFrame({"symbol": ["600002"]})
    calls: list[list[str]] = []

    def network(subset, **kwargs):
        requested = sorted(set(subset["symbol"].astype(str).str.zfill(6)))
        calls.append(requested)
        return MODULE.data_akshare.DownloadResult(
            prices=_price_rows(requested[0]),
            errors=pd.DataFrame(columns=["symbol", "error"]),
        )

    downloader = MODULE._checkpointed_stock_downloader(tmp_path, network)
    first = downloader(
        first_universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert set(first.prices["symbol"]) == {"600001"}

    second = downloader(
        second_universe,
        start_date="2024-01-01",
        end_date="2024-01-05",
        adjust="qfq",
        providers=("eastmoney", "tencent"),
    )
    assert set(second.prices["symbol"]) == {"600002"}
    assert calls == [["600001"], ["600002"]]


def test_official_index_checkpoint_reuses_verified_csv(tmp_path) -> None:
    calls = 0

    def network(index_code, **kwargs):
        nonlocal calls
        calls += 1
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "index_code": [index_code, index_code],
                "close": [100.0, 101.0],
                "provider": ["csindex:index_perf", "csindex:index_perf"],
            }
        )

    fetcher = MODULE._checkpointed_index_fetcher(tmp_path, network)
    first = fetcher("931152", start_date="2024-01-01", end_date="2024-01-05")
    assert calls == 1
    assert len(first) == 2
    second = fetcher("931152", start_date="2024-01-01", end_date="2024-01-05")
    assert calls == 1
    assert len(second) == 2


def test_baostock_success_stage_is_reused_for_same_universe(tmp_path) -> None:
    universe = pd.DataFrame(
        {
            "symbol": ["600001"],
            "effective_start": pd.to_datetime(["2024-01-01"]),
            "effective_end": pd.to_datetime(["2024-12-31"]),
        }
    )
    calls = 0

    def network_stage(frame, out):
        nonlocal calls
        calls += 1
        active = pd.DataFrame({"date": ["2024-01-02"], "symbol": ["600001"], "limit_pct": [10.0]})
        coverage = pd.DataFrame({"date": ["2024-01-02"], "coverage": [1.0]})
        active.to_csv(tmp_path / "active_limit_rows.csv", index=False)
        coverage.to_csv(tmp_path / "strict_daily_coverage.csv", index=False)
        return active, coverage, {"provider": "BaoStock", "strict_errors": []}

    stage = MODULE._checkpointed_baostock_stage(tmp_path, network_stage)
    first = stage(universe, tmp_path)
    assert calls == 1
    assert first[2]["provider"] == "BaoStock"
    second = stage(universe, tmp_path)
    assert calls == 1
    assert second[2]["provider"] == "BaoStock"
