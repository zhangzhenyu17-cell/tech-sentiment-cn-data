from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import time

import pandas as pd

from tech_sentiment import data_akshare
from tech_sentiment import eastmoney_fund_holdings_evidence as holdings
from tech_sentiment import sina_index_membership_evidence as sina


LEGACY_SCRIPT = Path(__file__).with_name("build_931152_oos_inputs.py")
CHECKPOINT_SCHEMA = 2
NETWORK_SPACING_SECONDS = 2.0
RATE_LIMIT_COOLDOWN_SECONDS = 20.0
RATE_LIMIT_OUTER_RETRIES = 2


def _load_legacy_module():
    spec = importlib.util.spec_from_file_location("build_931152_oos_inputs", LEGACY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen 931152 OOS input builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_output_dir(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-dir", type=Path, required=True)
    args, _ = parser.parse_known_args(argv)
    return args.output_dir


def _text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_meta(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _read_meta(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _frame_identity(frame: pd.DataFrame, *, sort_by: list[str] | None = None) -> str:
    copy = frame.copy()
    if sort_by:
        usable = [column for column in sort_by if column in copy.columns]
        if usable:
            copy = copy.sort_values(usable)
    copy = copy.reset_index(drop=True)
    payload = copy.to_csv(index=False, date_format="%Y-%m-%d")
    return _text_sha256(payload)


# ---- Sina membership checkpoint -------------------------------------------------

def _sina_checkpoint_paths(root: Path, symbol: str) -> tuple[Path, Path]:
    return root / f"sina_{symbol}.html", root / f"sina_{symbol}.meta.json"


def _validate_retained_page(html: str, *, symbol: str) -> None:
    compact = " ".join(html.replace("<", " <").replace(">", "> ").split())
    if symbol not in compact or "所属指数" not in compact or "指数代码" not in compact:
        raise ValueError(f"retained Sina page failed semantic validation for {symbol}")


def _read_sina_checkpoint(root: Path, symbol: str, *, index_code: str):
    html_path, meta_path = _sina_checkpoint_paths(root, symbol)
    meta = _read_meta(meta_path)
    if not html_path.is_file() or meta is None:
        return None
    if meta.get("schema_version") not in {1, CHECKPOINT_SCHEMA}:
        return None
    if meta.get("symbol") != symbol or meta.get("index_code") != index_code:
        return None
    source_url = sina.related_url(symbol)
    if meta.get("source_url") != source_url:
        return None
    html = html_path.read_text(encoding="utf-8")
    if meta.get("checkpoint_text_sha256") != _text_sha256(html):
        return None
    response_sha256 = str(meta.get("response_sha256") or "")
    if len(response_sha256) != 64:
        return None
    _validate_retained_page(html, symbol=symbol)
    intervals = sina.parse_membership_intervals(
        html,
        symbol=symbol,
        source_url=source_url,
        response_sha256=response_sha256,
        index_code=index_code,
    )
    return intervals, html


def _write_sina_checkpoint(
    root: Path,
    symbol: str,
    *,
    index_code: str,
    html: str,
    source_url: str,
    response_sha256: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    html_path, meta_path = _sina_checkpoint_paths(root, symbol)
    html_path.write_text(html, encoding="utf-8")
    _write_meta(
        meta_path,
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "symbol": symbol,
            "index_code": index_code,
            "source_url": source_url,
            "response_sha256": response_sha256,
            "checkpoint_text_sha256": _text_sha256(html),
            "evidence_role": "sina_related_index_page_transport_checkpoint_only",
        },
    )


def _checkpointed_sina_fetcher(root: Path):
    def fetch(symbol: str, *, timeout: int = 20, index_code: str = sina.CSI_931152):
        retained = _read_sina_checkpoint(root, symbol, index_code=index_code)
        if retained is not None:
            return retained

        time.sleep(NETWORK_SPACING_SECONDS)
        last_error: Exception | None = None
        for outer_attempt in range(RATE_LIMIT_OUTER_RETRIES + 1):
            try:
                html, source_url, response_sha256 = sina.fetch_related_page(
                    symbol,
                    timeout=timeout,
                    retries=2,
                    retry_backoff_seconds=2.0,
                )
                intervals = sina.parse_membership_intervals(
                    html,
                    symbol=symbol,
                    source_url=source_url,
                    response_sha256=response_sha256,
                    index_code=index_code,
                )
                _write_sina_checkpoint(
                    root,
                    symbol,
                    index_code=index_code,
                    html=html,
                    source_url=source_url,
                    response_sha256=response_sha256,
                )
                return intervals, html
            except Exception as exc:
                last_error = exc
                if "HTTP Error 456" not in str(exc) or outer_attempt >= RATE_LIMIT_OUTER_RETRIES:
                    raise
                time.sleep(RATE_LIMIT_COOLDOWN_SECONDS * (outer_attempt + 1))
        assert last_error is not None
        raise last_error

    return fetch


# ---- ETF holdings checkpoint ---------------------------------------------------

def _holdings_meta_path(root: Path, year: int) -> Path:
    return root / f"eastmoney_159992_{year}.meta.json"


def _checkpointed_holdings_fetcher(root: Path, network_fetcher):
    def fetch(fund_code: str, year: int, *, topline: int = 100, timeout: int = 30):
        text_path = root / f"eastmoney_{fund_code}_{year}.txt"
        meta_path = _holdings_meta_path(root, year)
        meta = _read_meta(meta_path)
        if (
            fund_code == holdings.TRACKING_ETF_931152
            and text_path.is_file()
            and meta is not None
            and meta.get("schema_version") == CHECKPOINT_SCHEMA
            and meta.get("fund_code") == fund_code
            and meta.get("year") == int(year)
            and meta.get("topline") == int(topline)
        ):
            text = text_path.read_text(encoding="utf-8")
            if meta.get("checkpoint_text_sha256") == _text_sha256(text):
                response_sha256 = str(meta.get("response_sha256") or "")
                source_url = str(meta.get("source_url") or "")
                if len(response_sha256) == 64 and source_url == holdings.holdings_url(fund_code, year, topline=topline):
                    html, years = holdings.parse_eastmoney_envelope(text)
                    batches = holdings.parse_holdings_html(
                        html,
                        fund_code=fund_code,
                        source_url=source_url,
                        response_sha256=response_sha256,
                    )
                    return batches, years, text

        batches, years, text = network_fetcher(
            fund_code,
            year,
            topline=topline,
            timeout=timeout,
        )
        if fund_code == holdings.TRACKING_ETF_931152 and batches:
            response_sha256 = batches[0].response_sha256
            source_url = batches[0].source_url
            if all(item.response_sha256 == response_sha256 and item.source_url == source_url for item in batches):
                text_path.write_text(text, encoding="utf-8")
                _write_meta(
                    meta_path,
                    {
                        "schema_version": CHECKPOINT_SCHEMA,
                        "fund_code": fund_code,
                        "year": int(year),
                        "topline": int(topline),
                        "source_url": source_url,
                        "response_sha256": response_sha256,
                        "checkpoint_text_sha256": _text_sha256(text),
                        "evidence_role": "eastmoney_tracking_fund_holdings_transport_checkpoint_only",
                    },
                )
        return batches, years, text

    return fetch


# ---- Stock-history checkpoint --------------------------------------------------

def _stock_meta_path(root: Path) -> Path:
    return root / "stock_prices_qfq.checkpoint.meta.json"


def _empty_stock_prices() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "symbol",
            "open",
            "close",
            "high",
            "low",
            "pct_chg",
            "amount",
            "board",
            "provider",
        ]
    )


def _checkpointed_stock_downloader(root: Path, network_downloader):
    def download(
        universe: pd.DataFrame,
        *,
        start_date: str,
        end_date: str,
        adjust: str = "",
        providers=("eastmoney", "tencent"),
        retries: int = 1,
        retry_backoff_seconds: float = 0.75,
        sleep_seconds: float = 0.0,
        timeout_seconds: float = 15.0,
        fail_fast: bool = False,
        client=None,
    ):
        price_path = root / "stock_prices_qfq.csv"
        error_path = root / "stock_download_errors.csv"
        meta_path = _stock_meta_path(root)
        requested = sorted(set(universe["symbol"].astype(str).str.zfill(6)))
        cached = _empty_stock_prices()
        meta = _read_meta(meta_path)
        if price_path.is_file() and meta is not None:
            expected = {
                "schema_version": CHECKPOINT_SCHEMA,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": adjust,
                "providers": list(providers),
                "requested_symbols": requested,
            }
            if all(meta.get(key) == value for key, value in expected.items()) and meta.get("file_sha256") == _file_sha256(price_path):
                try:
                    candidate = pd.read_csv(price_path, dtype={"symbol": str})
                except (pd.errors.EmptyDataError, ValueError):
                    candidate = _empty_stock_prices()
                required_cols = {"date", "symbol", "open", "close", "high", "low", "provider"}
                if required_cols.issubset(candidate.columns):
                    candidate["symbol"] = candidate["symbol"].astype(str).str.zfill(6)
                    if not candidate.empty:
                        candidate["date"] = pd.to_datetime(candidate["date"], errors="raise")
                        if candidate["date"].min() >= pd.Timestamp(start_date) and candidate["date"].max() <= pd.Timestamp(end_date):
                            cached = candidate
                    else:
                        cached = candidate

        cached_symbols = set(cached["symbol"].astype(str).str.zfill(6)) if not cached.empty else set()
        missing_symbols = sorted(set(requested) - cached_symbols)
        fresh = data_akshare.DownloadResult(prices=_empty_stock_prices(), errors=pd.DataFrame(columns=["symbol", "error"]))
        if missing_symbols:
            subset = universe[universe["symbol"].astype(str).str.zfill(6).isin(missing_symbols)].copy()
            fresh = network_downloader(
                subset,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
                providers=providers,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                sleep_seconds=sleep_seconds,
                timeout_seconds=timeout_seconds,
                fail_fast=fail_fast,
                client=client,
            )

        pieces = [frame for frame in (cached, fresh.prices) if frame is not None and not frame.empty]
        if pieces:
            combined = pd.concat(pieces, ignore_index=True)
        elif fresh.prices is not None and set(_empty_stock_prices().columns).issubset(fresh.prices.columns):
            combined = fresh.prices.copy()
        else:
            combined = _empty_stock_prices()
        if not combined.empty:
            combined["symbol"] = combined["symbol"].astype(str).str.zfill(6)
            combined["date"] = pd.to_datetime(combined["date"], errors="raise")
            combined = combined.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)

        errors = fresh.errors.copy()
        combined.to_csv(price_path, index=False, date_format="%Y-%m-%d")
        errors.to_csv(error_path, index=False)
        _write_meta(
            meta_path,
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": adjust,
                "providers": list(providers),
                "requested_symbols": requested,
                "file_sha256": _file_sha256(price_path),
                "evidence_role": "public_stock_history_transport_checkpoint_only",
            },
        )
        return data_akshare.DownloadResult(prices=combined, errors=errors)

    return download


# ---- Official CSI index checkpoint --------------------------------------------

def _index_meta_path(root: Path) -> Path:
    return root / "index_931152_prices.checkpoint.meta.json"


def _checkpointed_index_fetcher(root: Path, network_fetcher):
    def fetch(index_code: str, *, start_date: str, end_date: str, **kwargs):
        path = root / "index_931152_prices.csv"
        meta_path = _index_meta_path(root)
        meta = _read_meta(meta_path)
        if (
            index_code == "931152"
            and path.is_file()
            and meta is not None
            and meta.get("schema_version") == CHECKPOINT_SCHEMA
            and meta.get("index_code") == index_code
            and meta.get("start_date") == start_date
            and meta.get("end_date") == end_date
            and meta.get("file_sha256") == _file_sha256(path)
        ):
            frame = pd.read_csv(path, dtype={"index_code": str})
            required = {"date", "index_code", "provider"}
            if required.issubset(frame.columns) and not frame.empty:
                frame["date"] = pd.to_datetime(frame["date"], errors="raise")
                frame["index_code"] = frame["index_code"].astype(str).str.zfill(6)
                if set(frame["index_code"]) == {index_code} and frame["date"].min() >= pd.Timestamp(start_date) and frame["date"].max() <= pd.Timestamp(end_date):
                    return frame

        frame = network_fetcher(index_code, start_date=start_date, end_date=end_date, **kwargs)
        if index_code == "931152" and frame is not None and not frame.empty:
            frame.to_csv(path, index=False, date_format="%Y-%m-%d")
            _write_meta(
                meta_path,
                {
                    "schema_version": CHECKPOINT_SCHEMA,
                    "index_code": index_code,
                    "start_date": start_date,
                    "end_date": end_date,
                    "file_sha256": _file_sha256(path),
                    "evidence_role": "official_csi_index_history_transport_checkpoint_only",
                },
            )
        return frame

    return fetch


# ---- BaoStock successful-stage checkpoint -------------------------------------

def _baostock_meta_path(root: Path) -> Path:
    return root / "baostock_success.checkpoint.meta.json"


def _checkpointed_baostock_stage(root: Path, network_stage):
    def run(universe: pd.DataFrame, out: Path):
        active_path = root / "active_limit_rows.csv"
        coverage_path = root / "strict_daily_coverage.csv"
        meta_path = _baostock_meta_path(root)
        universe_sha = _frame_identity(universe, sort_by=["symbol", "effective_start", "effective_end"])
        meta = _read_meta(meta_path)
        if (
            active_path.is_file()
            and coverage_path.is_file()
            and meta is not None
            and meta.get("schema_version") == CHECKPOINT_SCHEMA
            and meta.get("universe_sha256") == universe_sha
            and meta.get("active_sha256") == _file_sha256(active_path)
            and meta.get("coverage_sha256") == _file_sha256(coverage_path)
            and isinstance(meta.get("limit_report"), dict)
        ):
            active = pd.read_csv(active_path)
            strict_daily = pd.read_csv(coverage_path)
            return active, strict_daily, dict(meta["limit_report"])

        active, strict_daily, report = network_stage(universe, out)
        _write_meta(
            meta_path,
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "universe_sha256": universe_sha,
                "active_sha256": _file_sha256(active_path),
                "coverage_sha256": _file_sha256(coverage_path),
                "limit_report": report,
                "evidence_role": "baostock_successful_stage_checkpoint_only",
            },
        )
        return active, strict_daily, report

    return run


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    output_dir = _parse_output_dir(effective_argv)
    output_dir.mkdir(parents=True, exist_ok=True)

    legacy = _load_legacy_module()
    legacy.fetch_membership_intervals = _checkpointed_sina_fetcher(output_dir)
    legacy.SINA_SECOND_PASS_SLEEP_SECONDS = RATE_LIMIT_COOLDOWN_SECONDS

    legacy.fetch_and_parse_holdings_year = _checkpointed_holdings_fetcher(
        output_dir, legacy.fetch_and_parse_holdings_year
    )
    legacy.download_universe_history = _checkpointed_stock_downloader(
        output_dir, legacy.download_universe_history
    )
    legacy.fetch_csindex_history = _checkpointed_index_fetcher(
        output_dir, legacy.fetch_csindex_history
    )
    legacy._fetch_baostock_limit_inputs = _checkpointed_baostock_stage(
        output_dir, legacy._fetch_baostock_limit_inputs
    )

    original_argv = sys.argv
    try:
        sys.argv = [str(LEGACY_SCRIPT), *effective_argv]
        return int(legacy.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
