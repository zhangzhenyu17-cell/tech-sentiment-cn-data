from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .capital_input_data import (
    ExchangeTurnoverFetchResult,
    EtfShareFetchResult,
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
)
from .prospective_capture_timing_v3 import validate_capture_pair_v3
from .v4c03_szse_etf_shares import (
    SzseEtfShareFetchResult,
    fetch_szse_etf_share_history,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SOURCE_KEYS = ("SSE_588000", "SZSE_159915", "SSE_TURNOVER", "SZSE_TURNOVER")
ALLOWED_TRANSPORT_ORIGINS = ("GITHUB_HOSTED", "GITHUB_HOSTED_MACOS_FALLBACK")


@dataclass(frozen=True)
class SourceObservation:
    source_key: str
    state: str
    root: Path
    receipt: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False)


def _date_rows(frame: pd.DataFrame, market_session_date: str) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame.iloc[0:0].copy()
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    return frame[dates.eq(pd.Timestamp(market_session_date).normalize())].copy()


def _receipt(
    *,
    source_key: str,
    state: str,
    market_session_date: str,
    decision_date: str,
    observed_at: datetime,
    attempt_started_at: datetime,
    timing: dict[str, object],
    source_commit: str,
    transport_origin: str,
    runner_name: str,
    row_count: int,
    error_count: int,
) -> dict[str, Any]:
    complete = state == "COMPLETE"
    return {
        "schema_version": "prospective-source-observation-v3",
        "source_key": source_key,
        "state": state,
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "first_observed_at_asia_shanghai": (
            observed_at.astimezone(SHANGHAI).isoformat() if complete else None
        ),
        "observation_timestamp_semantics": "SOURCE_FETCH_COMPLETION_TIME",
        "observation_attempt_at_asia_shanghai": attempt_started_at.astimezone(SHANGHAI).isoformat(),
        "source_fetch_completed_at_asia_shanghai": observed_at.astimezone(SHANGHAI).isoformat(),
        "source_available_at": None,
        "source_available_at_status": "UNVERIFIED",
        "transport_origin": transport_origin,
        "runner_name": runner_name,
        "producer_git_sha": source_commit,
        "row_count": int(row_count),
        "error_count": int(error_count),
        "timing_class": timing["timing_class"],
        "shadow_decision_eligible": bool(
            complete and timing["shadow_decision_eligible_by_time"]
        ),
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "forward_outcomes_read": False,
        "private_model_semantics_materialized": False,
        "portfolio_or_holdings_data_included": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }


def capture_capital_source_observations_v3(
    *,
    market_session_date: str,
    decision_date: str,
    source_commit: str,
    output_root: Path,
    observed_at: datetime | None = None,
    client: Any | None = None,
    transport_origin: str = "GITHUB_HOSTED",
    runner_name: str = "",
    sse_etf_fetcher: Callable[..., EtfShareFetchResult] = fetch_sse_etf_share_history,
    szse_etf_fetcher: Callable[..., SzseEtfShareFetchResult] = fetch_szse_etf_share_history,
    turnover_fetcher: Callable[..., ExchangeTurnoverFetchResult] = fetch_sse_szse_a_share_turnover_history,
    source_keys: tuple[str, ...] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, SourceObservation]:
    clock_fn = clock or (lambda: datetime.now(SHANGHAI))
    attempt_started_at = observed_at or clock_fn()
    if attempt_started_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if transport_origin not in ALLOWED_TRANSPORT_ORIGINS:
        raise ValueError(f"unsupported V3 transport origin: {transport_origin}")
    if client is None:
        import akshare as ak  # type: ignore
        client = ak
    timing = validate_capture_pair_v3(
        market_session_date=market_session_date,
        decision_date=decision_date,
        observed_at=attempt_started_at,
        client=client,
    )
    target = pd.DatetimeIndex([pd.Timestamp(market_session_date).normalize()])
    selected = tuple(source_keys or SOURCE_KEYS)
    unknown = sorted(set(selected) - set(SOURCE_KEYS))
    if unknown:
        raise ValueError(f"unknown V3 source keys: {unknown}")
    if not selected:
        return {}

    specs: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    source_observed_at: dict[str, datetime] = {}
    if "SSE_588000" in selected:
        sse = sse_etf_fetcher(
            trading_dates=target,
            fund_codes=["588000"],
            sleep_seconds=0,
        )
        source_observed_at["SSE_588000"] = clock_fn()
        sse_rows = _date_rows(sse.data, market_session_date)
        if "fund_code" in sse_rows.columns:
            sse_rows = sse_rows[
                sse_rows["fund_code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6).eq("588000")
            ].copy()
        specs["SSE_588000"] = (sse_rows, sse.errors.copy())

    if "SZSE_159915" in selected:
        szse = szse_etf_fetcher(
            start_date=market_session_date,
            end_date=market_session_date,
            trading_dates=target,
            fund_codes=["159915"],
            sleep_seconds=0,
            client=client,
        )
        source_observed_at["SZSE_159915"] = clock_fn()
        szse_rows = _date_rows(szse.data, market_session_date)
        if "fund_code" in szse_rows.columns:
            szse_rows = szse_rows[
                szse_rows["fund_code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6).eq("159915")
            ].copy()
        specs["SZSE_159915"] = (szse_rows, szse.errors.copy())

    if {"SSE_TURNOVER", "SZSE_TURNOVER"} & set(selected):
        turnover = turnover_fetcher(
            trading_dates=target,
            sleep_seconds=0,
        )
        turnover_completed_at = clock_fn()
        if "SSE_TURNOVER" in selected:
            source_observed_at["SSE_TURNOVER"] = turnover_completed_at
        if "SZSE_TURNOVER" in selected:
            source_observed_at["SZSE_TURNOVER"] = turnover_completed_at
        turnover_errors = turnover.errors.copy()
        sse_errors = (
            turnover_errors[turnover_errors["exchange"].astype(str).eq("SSE")].copy()
            if not turnover_errors.empty and "exchange" in turnover_errors.columns
            else turnover_errors.iloc[0:0].copy()
        )
        szse_errors = (
            turnover_errors[turnover_errors["exchange"].astype(str).eq("SZSE")].copy()
            if not turnover_errors.empty and "exchange" in turnover_errors.columns
            else turnover_errors.iloc[0:0].copy()
        )
        if "SSE_TURNOVER" in selected:
            specs["SSE_TURNOVER"] = (_date_rows(turnover.sse, market_session_date), sse_errors)
        if "SZSE_TURNOVER" in selected:
            specs["SZSE_TURNOVER"] = (_date_rows(turnover.szse, market_session_date), szse_errors)

    output_root.mkdir(parents=True, exist_ok=True)
    observations: dict[str, SourceObservation] = {}
    for source_key in selected:
        data, errors = specs[source_key]
        state = "COMPLETE" if len(data) > 0 and errors.empty else "SOURCE_FAILURE_OR_INCOMPLETE"
        root = output_root / source_key
        root.mkdir(parents=True, exist_ok=True)
        _write_frame(data, root / "data.csv")
        _write_frame(errors, root / "errors.csv")
        observed = source_observed_at[source_key]
        source_timing = validate_capture_pair_v3(
            market_session_date=market_session_date,
            decision_date=decision_date,
            observed_at=observed,
            client=client,
        )
        receipt = _receipt(
            source_key=source_key,
            state=state,
            market_session_date=market_session_date,
            decision_date=decision_date,
            observed_at=observed,
            attempt_started_at=attempt_started_at,
            timing=source_timing,
            source_commit=source_commit,
            transport_origin=transport_origin,
            runner_name=runner_name,
            row_count=len(data),
            error_count=len(errors),
        )
        (root / "SOURCE_OBSERVATION_RECEIPT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        observations[source_key] = SourceObservation(
            source_key=source_key,
            state=state,
            root=root,
            receipt=receipt,
        )

    summary = {
        "schema_version": "prospective-source-observation-attempt-v3",
        "activation_mode": "FORMAL_CAPTURE_SOURCE_NO_SOURCE_LEVEL_HANDOFF",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "attempt_started_at_asia_shanghai": attempt_started_at.astimezone(SHANGHAI).isoformat(),
        "attempt_timing": timing,
        "observation_timestamp_semantics": "SOURCE_FETCH_COMPLETION_TIME",
        "requested_source_keys": list(selected),
        "source_states": {key: observations[key].state for key in selected},
        "complete_source_count": sum(
            observation.state == "COMPLETE" for observation in observations.values()
        ),
        "required_source_count": len(selected),
        "all_sources_complete": all(
            observation.state == "COMPLETE" for observation in observations.values()
        ),
        "formal_evidence_handoff": False,
        "forward_outcomes_read": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (output_root / "SOURCE_OBSERVATION_ATTEMPT.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return observations


def package_complete_source_observation_v3(
    observation: SourceObservation,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    if observation.state != "COMPLETE":
        raise ValueError("only COMPLETE source observations may be packaged")
    receipt = observation.receipt
    market_session_date = str(receipt["market_session_date"])
    source_key = observation.source_key
    tag = f"prospective-source-observation-v3-{market_session_date}-{source_key}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{tag}.tar.gz"
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                for path in sorted(observation.root.rglob("*")):
                    if not path.is_file():
                        continue
                    info = tar.gettarinfo(
                        str(path), arcname=f"source_observation/{path.name}"
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
    checksum = _sha256(archive)
    checksum_path = output_dir / f"{tag}.sha256"
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    receipt_path = observation.root / "SOURCE_OBSERVATION_RECEIPT.json"
    identity = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "prospective-source-observation-package-v3",
        "release_tag": tag,
        "source_key": source_key,
        "market_session_date": market_session_date,
        "decision_date": receipt["decision_date"],
        "source_observation_identity": identity,
        "archive": archive.name,
        "archive_sha256": checksum,
        "receipt_sha256": _sha256(receipt_path),
        "first_observed_at_asia_shanghai": receipt["first_observed_at_asia_shanghai"],
        "observation_timestamp_semantics": receipt["observation_timestamp_semantics"],
        "shadow_decision_eligible": receipt["shadow_decision_eligible"],
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
    }
    manifest_path = output_dir / f"{tag}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "SOURCE_KEYS",
    "SourceObservation",
    "capture_capital_source_observations_v3",
    "package_complete_source_observation_v3",
]
