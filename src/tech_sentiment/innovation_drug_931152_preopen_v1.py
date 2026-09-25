from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile
from typing import Any, Callable

import pandas as pd

from .csindex_index_price import fetch_csindex_history
from .data_akshare import download_universe_history, fetch_current_csindex_universe
from .prospective_preopen_timing_v2 import (
    SHANGHAI,
    a_share_trading_dates,
    first_a_share_trading_day_after,
    validate_preopen_capture_window,
)
from .sector_limit_coverage_strict import audit_strict_member_day_limit_coverage
from .sector_limit_pipeline import build_and_audit_sector_limit_rows
from .universe import apply_universe_membership

INDEX_CODE = "931152"
EXPECTED_CONSTITUENTS = 50
MIN_DAILY_LIMIT_COVERAGE = 0.95
WARMUP_CALENDAR_DAYS = 550
EARLIEST_OPERATIONAL_CAPTURE = time(23, 45)
PROSPECTIVE_FREEZE_DATE = "2026-09-25"
FIRST_PROSPECTIVE_MARKET_SESSION_RULE = "FIRST_CONFIRMED_A_SHARE_TRADING_DAY_STRICTLY_AFTER_PROSPECTIVE_FREEZE_DATE"
BAOSTOCK_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,isST"
)
PACKAGE_SCHEMA = "innovation-drug-931152-preopen-public-v1"
PACKAGE_FILES = (
    "membership_snapshot.csv",
    "trading_calendar_dates.csv",
    "stock_prices_qfq.csv",
    "stock_download_errors.csv",
    "active_limit_rows.csv",
    "strict_daily_coverage.csv",
    "index_931152_prices.csv",
    "report.json",
)


@dataclass(frozen=True)
class PublicPreopenResult:
    release_tag: str
    archive_path: Path
    checksum_path: Path
    manifest_path: Path
    report_path: Path


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _deterministic_tar_gz(files: list[tuple[str, bytes]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted(files, key=lambda item: item[0]):
                    info = tarfile.TarInfo(name=name)
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))


def _bool01(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _baostock_code(symbol: str) -> str:
    text = str(symbol).strip().zfill(6)
    return f"sh.{text}" if text.startswith(("6", "9")) else f"sz.{text}"


def _baostock_rows(result: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    return rows


def _query_rows(query: Callable[[], Any], *, stage: str, retries: int = 2) -> tuple[list[list[str]], list[str]]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = query()
            rows = _baostock_rows(result)
            if rows:
                return rows, list(result.fields)
            last = ValueError(f"BaoStock {stage} returned no rows")
        except Exception as exc:
            last = exc
        if attempt == retries:
            break
    raise RuntimeError(f"BaoStock {stage} exhausted retries") from last


def load_anchor_symbols(path: str | Path) -> tuple[str, ...]:
    frame = pd.read_csv(path, dtype={"symbol": str})
    if list(frame.columns) != ["symbol"]:
        raise ValueError("931152 live anchor must contain exactly one symbol column")
    symbols = tuple(sorted(frame["symbol"].astype(str).str.zfill(6)))
    if len(symbols) != EXPECTED_CONSTITUENTS or len(set(symbols)) != EXPECTED_CONSTITUENTS:
        raise ValueError("931152 live anchor must contain exactly 50 unique symbols")
    return symbols


def validate_live_snapshot(
    snapshot: pd.DataFrame,
    *,
    anchor_symbols: tuple[str, ...],
    market_session_date: str,
) -> pd.DataFrame:
    required = {"symbol", "source_index", "snapshot_source", "snapshot_role"}
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"live 931152 snapshot missing columns: {sorted(missing)}")
    frame = snapshot.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    if set(frame["source_index"].astype(str).str.zfill(6)) != {INDEX_CODE}:
        raise ValueError("live snapshot contains a non-931152 source index")
    if len(frame) != EXPECTED_CONSTITUENTS or frame["symbol"].nunique() != EXPECTED_CONSTITUENTS:
        raise ValueError("live 931152 snapshot does not contain exactly 50 unique members")
    roles = set(frame["snapshot_role"].astype(str))
    if roles != {"official_live_witness"}:
        raise ValueError(f"931152 prospective input requires an official CSI live witness, got {sorted(roles)}")
    observed = tuple(sorted(frame["symbol"]))
    if observed != tuple(anchor_symbols):
        missing_anchor = sorted(set(anchor_symbols) - set(observed))
        extra_live = sorted(set(observed) - set(anchor_symbols))
        raise ValueError(
            "931152 live membership differs from frozen 2026-09-11 continuity anchor; "
            f"missing={missing_anchor}, extra={extra_live}. Fail closed until membership is requalified."
        )
    day = pd.Timestamp(market_session_date).normalize()
    frame["effective_start"] = day
    frame["effective_end"] = day
    frame["universe_mode"] = "point_in_time"
    frame["qualification_scope"] = "prospective_current_market_session_only"
    return frame.sort_values("symbol").reset_index(drop=True)


def _fetch_current_limit_rows(
    universe: pd.DataFrame,
    *,
    market_session_date: str,
    bs: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if bs is None:
        try:
            import baostock as bs  # type: ignore
            from importlib.metadata import version
        except ImportError as exc:
            raise RuntimeError("install sector-data extra") from exc
        provider_version = version("baostock")
    else:
        provider_version = str(getattr(bs, "__version__", "test-double"))

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    history_parts: list[pd.DataFrame] = []
    basic_parts: list[pd.DataFrame] = []
    failures: list[str] = []
    try:
        cal_rows, cal_fields = _query_rows(
            lambda: bs.query_trade_dates(start_date=market_session_date, end_date=market_session_date),
            stage="trade_calendar",
        )
        calendar = pd.DataFrame(cal_rows, columns=cal_fields)
        trading = calendar[calendar["is_trading_day"].map(_bool01)]
        if len(trading) != 1:
            raise ValueError("BaoStock does not confirm market_session_date as a trading day")

        for symbol in sorted(set(universe["symbol"].astype(str).str.zfill(6))):
            code = _baostock_code(symbol)
            try:
                rows, fields = _query_rows(
                    lambda code=code: bs.query_history_k_data_plus(
                        code,
                        BAOSTOCK_FIELDS,
                        start_date=market_session_date,
                        end_date=market_session_date,
                        frequency="d",
                        adjustflag="3",
                    ),
                    stage=f"history:{code}",
                )
                history_parts.append(pd.DataFrame(rows, columns=fields))
                rows, fields = _query_rows(
                    lambda code=code: bs.query_stock_basic(code=code),
                    stage=f"stock_basic:{code}",
                )
                basic_parts.append(pd.DataFrame(rows, columns=fields))
            except Exception as exc:
                failures.append(f"{code}: {type(exc).__name__}: {exc}")
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    if failures:
        raise ValueError("BaoStock current-day member fetch failed: " + " | ".join(failures[:5]))
    history = pd.concat(history_parts, ignore_index=True) if history_parts else pd.DataFrame()
    basic = pd.concat(basic_parts, ignore_index=True) if basic_parts else pd.DataFrame()
    if history.empty or basic.empty:
        raise ValueError("BaoStock current-day limit inputs are incomplete")

    result = build_and_audit_sector_limit_rows(
        history,
        basic,
        universe,
        min_daily_coverage=MIN_DAILY_LIMIT_COVERAGE,
    )
    enriched = result.rows.copy()
    enriched["symbol"] = enriched["code"].astype(str).str.replace(r"^(?:sh|sz)\.", "", regex=True)
    strict = audit_strict_member_day_limit_coverage(
        enriched,
        universe,
        [pd.Timestamp(market_session_date)],
        min_daily_coverage=MIN_DAILY_LIMIT_COVERAGE,
    )
    if not strict.eligible:
        raise ValueError("strict current-day limit coverage failed: " + "; ".join(strict.errors))
    active = apply_universe_membership(enriched, universe)
    report = {
        "provider": "BaoStock",
        "provider_version": provider_version,
        "expected_member_days": strict.expected_member_days,
        "observed_member_days": strict.observed_member_days,
        "eligible_member_days": strict.eligible_member_days,
        "missing_member_days": strict.missing_member_days,
        "minimum_daily_observation_coverage": strict.minimum_daily_observation_coverage,
        "minimum_daily_limit_coverage": strict.minimum_daily_coverage,
        "required_daily_limit_coverage": strict.required_daily_coverage,
        "days_below_threshold": strict.days_below_threshold,
        "strict_errors": list(strict.errors),
    }
    return active, strict.daily_coverage, report


def _package(
    *,
    raw_dir: Path,
    package_dir: Path,
    market_session_date: str,
    decision_date: str,
    source_commit: str,
) -> PublicPreopenResult:
    release_tag = f"innovation-drug-931152-preopen-v1-{market_session_date}-for-{decision_date}"
    package_dir.mkdir(parents=True, exist_ok=True)
    file_rows: list[dict[str, Any]] = []
    archive_items: list[tuple[str, bytes]] = []
    for name in PACKAGE_FILES:
        path = raw_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing 931152 preopen package file: {name}")
        data = path.read_bytes()
        file_rows.append({"path": name, "bytes": len(data), "sha256": _sha256_bytes(data)})
        archive_items.append((f"innovation_drug_931152_preopen/{name}", data))

    manifest_core = {
        "schema_version": PACKAGE_SCHEMA,
        "release_tag": release_tag,
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "source_repository": "zhangzhenyu17-cell/tech-sentiment-cn-data",
        "source_commit": source_commit,
        "index_code": INDEX_CODE,
        "files": file_rows,
        "contains_model_output": False,
        "contains_private_thresholds_or_signals": False,
        "contains_forward_outcomes": False,
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
    }
    manifest_core["bundle_identity"] = _sha256_bytes(_canonical_json(manifest_core))
    archive_path = package_dir / f"{release_tag}.tar.gz"
    _deterministic_tar_gz(archive_items, archive_path)
    archive_sha = _sha256_file(archive_path)
    manifest = {**manifest_core, "archive_sha256": archive_sha}
    manifest_path = package_dir / f"{release_tag}.manifest.json"
    manifest_path.write_bytes(_canonical_json(manifest))
    checksum_path = package_dir / f"{release_tag}.sha256"
    checksum_path.write_text(f"{archive_sha}  {archive_path.name}\n", encoding="utf-8")
    return PublicPreopenResult(
        release_tag=release_tag,
        archive_path=archive_path,
        checksum_path=checksum_path,
        manifest_path=manifest_path,
        report_path=raw_dir / "report.json",
    )


def build_public_preopen_capture(
    *,
    market_session_date: str,
    decision_date: str,
    anchor_path: str | Path,
    output_dir: str | Path,
    package_dir: str | Path,
    source_commit: str,
    captured_at: datetime,
    client: Any | None = None,
    bs: Any | None = None,
) -> PublicPreopenResult:
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    local = captured_at.astimezone(SHANGHAI)
    earliest = datetime.combine(
        pd.Timestamp(market_session_date).date(),
        EARLIEST_OPERATIONAL_CAPTURE,
        tzinfo=SHANGHAI,
    )
    if local < earliest:
        raise ValueError("931152 pre-open capture may not begin before 23:45 Asia/Shanghai on T")

    if client is None:
        try:
            import akshare as client  # type: ignore
        except ImportError as exc:
            raise RuntimeError("install data extra") from exc
    timing = validate_preopen_capture_window(
        market_session_date=market_session_date,
        decision_date=decision_date,
        captured_at=captured_at,
        client=client,
    )
    first_prospective_market_session = first_a_share_trading_day_after(
        cutoff_date=PROSPECTIVE_FREEZE_DATE,
        client=client,
    )
    if pd.Timestamp(market_session_date).normalize() < pd.Timestamp(first_prospective_market_session):
        raise ValueError(
            "historical backfill before the first confirmed A-share session after the frozen V1 cutoff is forbidden"
        )
    calendar_dates = a_share_trading_dates(client)
    timing = {
        **timing,
        "prospective_freeze_date": PROSPECTIVE_FREEZE_DATE,
        "first_prospective_market_session_rule": FIRST_PROSPECTIVE_MARKET_SESSION_RULE,
        "first_prospective_market_session": first_prospective_market_session,
        "trading_calendar_source": "akshare.tool_trade_date_hist_sina",
    }
    anchor_symbols = load_anchor_symbols(anchor_path)
    snapshot = fetch_current_csindex_universe([INDEX_CODE], client=client)
    universe = validate_live_snapshot(
        snapshot,
        anchor_symbols=anchor_symbols,
        market_session_date=market_session_date,
    )

    day = pd.Timestamp(market_session_date)
    start = (day - pd.Timedelta(days=WARMUP_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    download = download_universe_history(
        universe,
        start_date=start,
        end_date=market_session_date,
        adjust="qfq",
        providers=("eastmoney", "tencent"),
        retries=2,
        retry_backoff_seconds=1.0,
        sleep_seconds=0.05,
        timeout_seconds=20.0,
        fail_fast=False,
        client=client,
    )
    prices = download.prices.copy()
    errors = download.errors.copy()
    if not errors.empty:
        raise ValueError(f"current 931152 qfq warm-up has {len(errors)} symbol download errors")
    prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    latest = prices[prices["date"].eq(day.normalize())]
    if set(latest["symbol"]) != set(anchor_symbols):
        raise ValueError("current-day qfq rows do not cover the exact 931152 live membership")
    required_price_columns = {
        "date", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"
    }
    missing_price_columns = required_price_columns - set(prices.columns)
    if missing_price_columns:
        raise ValueError(
            f"current 931152 qfq input missing required fields: {sorted(missing_price_columns)}"
        )

    active_limits, strict_daily, limit_report = _fetch_current_limit_rows(
        universe,
        market_session_date=market_session_date,
        bs=bs,
    )
    required_limit_columns = {"date", "symbol", "tradestatus", "isST", "limit_pct", "limit_eligible"}
    missing_limit_columns = required_limit_columns - set(active_limits.columns)
    if missing_limit_columns:
        raise ValueError(
            f"current 931152 BaoStock status input missing required fields: {sorted(missing_limit_columns)}"
        )

    index = fetch_csindex_history(
        INDEX_CODE,
        start_date=market_session_date,
        end_date=market_session_date,
        retries=4,
        retry_backoff_seconds=1.0,
        timeout_seconds=20.0,
    )
    if index.empty:
        raise ValueError("official 931152 current-session index rail is empty")
    index["date"] = pd.to_datetime(index["date"], errors="raise").dt.normalize()
    exact_index = index[index["date"].eq(day.normalize())].copy()
    if len(exact_index) != 1:
        raise ValueError("official 931152 rail must contain exactly one current-session row")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    universe.to_csv(out / "membership_snapshot.csv", index=False, date_format="%Y-%m-%d")
    pd.DataFrame({"trade_date": calendar_dates.strftime("%Y-%m-%d")}).to_csv(
        out / "trading_calendar_dates.csv", index=False
    )
    prices.to_csv(out / "stock_prices_qfq.csv", index=False, date_format="%Y-%m-%d")
    errors.to_csv(out / "stock_download_errors.csv", index=False)
    active_limits.to_csv(out / "active_limit_rows.csv", index=False, date_format="%Y-%m-%d")
    strict_daily.to_csv(out / "strict_daily_coverage.csv", index=False, date_format="%Y-%m-%d")
    exact_index.to_csv(out / "index_931152_prices.csv", index=False, date_format="%Y-%m-%d")

    report = {
        "schema_version": PACKAGE_SCHEMA,
        "status": "QUALIFIED_PUBLIC_PREOPEN_INPUT",
        "qualification_scope": "CURRENT_SESSION_PUBLIC_RAW_INPUT_ONLY",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "index_code": INDEX_CODE,
        "source_commit": source_commit,
        "timing": timing,
        "membership": {
            "expected_constituents": EXPECTED_CONSTITUENTS,
            "observed_constituents": int(universe["symbol"].nunique()),
            "anchor_date": "2026-09-11",
            "anchor_source_artifact_id": 10506915060,
            "anchor_source_artifact_sha256": "1901c168222fb718277785b62815d1db03b5b4c016efeb91a6d9dbead60b11cd",
            "anchor_exact_match": True,
            "snapshot_sources": sorted(set(universe["snapshot_source"].astype(str))),
            "snapshot_roles": sorted(set(universe["snapshot_role"].astype(str))),
        },
        "stock_prices": {
            "adjustment": "qfq",
            "warmup_calendar_days": WARMUP_CALENDAR_DAYS,
            "rows": int(len(prices)),
            "symbols": int(prices["symbol"].nunique()),
            "current_session_symbols": int(latest["symbol"].nunique()),
            "download_error_rows": int(len(errors)),
            "required_current_session_price_fields": sorted(required_price_columns),
            "required_current_session_status_fields": ["tradestatus", "isST"],
        },
        "limit_rule": limit_report,
        "index": {
            "rows": int(len(exact_index)),
            "providers": sorted(set(exact_index["provider"].astype(str)))
            if "provider" in exact_index
            else [],
        },
        "privacy_and_evidence_firewall": {
            "model_events_computed": False,
            "private_model_thresholds_or_signals_included": False,
            "forward_outcomes_computed": False,
            "historical_backfill_performed": False,
            "production_permission_granted": False,
            "trading_authority_granted": False,
        },
    }
    (out / "report.json").write_bytes(_canonical_json(report))
    return _package(
        raw_dir=out,
        package_dir=Path(package_dir),
        market_session_date=market_session_date,
        decision_date=decision_date,
        source_commit=source_commit,
    )


__all__ = [
    "EXPECTED_CONSTITUENTS",
    "FIRST_PROSPECTIVE_MARKET_SESSION_RULE",
    "PROSPECTIVE_FREEZE_DATE",
    "INDEX_CODE",
    "PACKAGE_SCHEMA",
    "PublicPreopenResult",
    "build_public_preopen_capture",
    "load_anchor_symbols",
    "validate_live_snapshot",
]
