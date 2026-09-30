from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

SCHEMA_VERSION = "market-regime-broad-market-capital-v0-public-input-v1"
PRODUCT_ID = "MARKET_REGIME_BROAD_MARKET_CAPITAL_V0_PUBLIC_INPUT"
SCOPE = "SSE_SZSE_A_SHARES"
STATUS_READY = "PUBLIC_RAW_INPUT_READY_FOR_PRIVATE_BROAD_MARKET_CAPITAL_V0"
STATUS_INSUFFICIENT = "PUBLIC_RAW_INPUT_DATA_INSUFFICIENT"


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _dates(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    _require("date" in frame.columns, f"{label} missing date")
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    _require(not out["date"].duplicated().any(), f"{label} duplicate dates")
    return out.sort_values("date").reset_index(drop=True)


def normalize_turnover(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date", "sse_a_share_turnover_yuan", "szse_a_share_turnover_yuan",
        "amount", "scope", "canonical_all_a_state",
    }
    _require(not (required - set(frame.columns)), "turnover fields missing")
    out = _dates(frame[list(required)], "turnover")
    for col in ("sse_a_share_turnover_yuan", "szse_a_share_turnover_yuan", "amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
        _require(out[col].notna().all() and (out[col] > 0).all(), f"invalid turnover {col}")
    _require(out["scope"].eq(SCOPE).all(), "turnover scope drift")
    _require(out["canonical_all_a_state"].eq("INCOMPLETE_BSE_NOT_INCLUDED").all(), "turnover BSE scope drift")
    expected = out["sse_a_share_turnover_yuan"] + out["szse_a_share_turnover_yuan"]
    _require(((out["amount"] - expected).abs() <= 1.0).all(), "turnover sum mismatch")
    return out.sort_values("date").reset_index(drop=True)


def normalize_financing(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date", "sse_financing_balance_yuan", "szse_financing_balance_yuan",
        "financing_balance_yuan", "sse_source_unit", "szse_source_unit",
        "canonical_unit", "sse_source_identity", "szse_source_identity",
        "sse_source_url", "szse_source_url",
    }
    _require(not (required - set(frame.columns)), "financing fields missing")
    out = _dates(frame[list(required)], "financing")
    for col in ("sse_financing_balance_yuan", "szse_financing_balance_yuan", "financing_balance_yuan"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
        _require(out[col].notna().all() and (out[col] > 0).all(), f"invalid financing {col}")
    _require(out["sse_source_unit"].eq("CNY").all(), "SSE financing unit drift")
    _require(out["szse_source_unit"].eq("CNY_100M").all(), "SZSE financing source-unit drift")
    _require(out["canonical_unit"].eq("CNY").all(), "financing canonical-unit drift")
    expected = out["sse_financing_balance_yuan"] + out["szse_financing_balance_yuan"]
    _require(((out["financing_balance_yuan"] - expected).abs() <= 1.0).all(), "financing sum mismatch")
    return out.sort_values("date").reset_index(drop=True)


def append_exact_history(seed: pd.DataFrame, fresh: pd.DataFrame, *, label: str) -> pd.DataFrame:
    left = _dates(seed, f"{label}_seed")
    right = _dates(fresh, f"{label}_fresh") if len(fresh) else fresh.copy()
    if len(right):
        overlap = sorted(set(left["date"]) & set(right["date"]))
        _require(not overlap, f"{label} seed/fresh overlap: {overlap[:3]}")
        out = pd.concat([left, right], ignore_index=True, sort=False)
    else:
        out = left.copy()
    _require(not out["date"].duplicated().any(), f"{label} combined duplicate dates")
    return out.sort_values("date").reset_index(drop=True)


def build_summary(
    turnover: pd.DataFrame,
    financing: pd.DataFrame,
    *,
    trading_calendar: pd.DatetimeIndex,
    market_date: str,
    fresh_turnover_error_rows: int,
    fresh_financing_error_rows: int,
    seed_lineage: Mapping[str, Any],
) -> dict[str, Any]:
    t = normalize_turnover(turnover)
    f = normalize_financing(financing)
    cal = pd.DatetimeIndex(pd.to_datetime(trading_calendar, errors="raise")).normalize().sort_values().unique()
    target = pd.Timestamp(market_date).normalize()
    _require(target in cal, "market_date missing from trading calendar")
    cal = cal[cal <= target]
    _require(len(cal) > 60, "trading calendar too short")
    _require(set(t["date"]) == set(cal), "turnover must cover every trading date through market_date")
    latest_financing = pd.Timestamp(f["date"].max()).normalize()
    _require(latest_financing <= target, "future financing row detected")
    financing_missing_dates = [d for d in cal if d not in set(f["date"])]
    # Publication-lag policy: financing may be unavailable for the current market
    # date only. Any older gap makes the rail fail closed.
    older_missing = [d for d in financing_missing_dates if d < target]
    latest_idx = int(list(cal).index(latest_financing))
    target_idx = len(cal) - 1
    lag = target_idx - latest_idx
    ready = (
        t["date"].max() == target
        and not older_missing
        and lag in {0, 1}
        and fresh_turnover_error_rows == 0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": PRODUCT_ID,
        "status": STATUS_READY if ready else STATUS_INSUFFICIENT,
        "market_date": str(target.date()),
        "scope": SCOPE,
        "trading_day_count": int(len(cal)),
        "turnover": {
            "latest_observation_date": str(pd.Timestamp(t["date"].max()).date()),
            "row_count": int(len(t)),
            "complete_through_market_date": bool(set(t["date"]) == set(cal)),
            "fresh_capture_error_rows": int(fresh_turnover_error_rows),
            "bse_included": False,
            "full_sh_sz_bj_market_claimed": False,
        },
        "financing": {
            "latest_available_observation_date": str(latest_financing.date()),
            "row_count": int(len(f)),
            "publication_lag_trading_days": int(lag),
            "same_observation_date_as_market_date": bool(latest_financing == target),
            "current_market_date_missing_allowed_as_publication_lag": True,
            "max_allowed_publication_lag_trading_days": 1,
            "older_missing_date_count": int(len(older_missing)),
            "fresh_capture_error_rows": int(fresh_financing_error_rows),
        },
        "same_day_operational_readiness": {
            "ready": bool(ready),
            "meaning": "RAIL_CAN_BE_BUILT_ON_MARKET_DATE_USING_SAME_DAY_TURNOVER_AND_LATEST_OFFICIAL_FINANCING_WITH_AT_MOST_ONE_TRADING_DAY_PUBLICATION_LAG",
            "full_same_observation_date_claimed": False,
            "no_forward_fill": True,
            "no_backfill_from_future_publication": True,
        },
        "seed_lineage": dict(seed_lineage),
        "authority": {
            "contains_model_output": False,
            "contains_private_thresholds": False,
            "contains_private_evidence": False,
            "forward_outcomes_read": False,
            "evidence_qualification_changed": False,
            "production_permission_changed": False,
            "trading_authority": False,
        },
    }


def validate_summary(payload: Mapping[str, Any]) -> None:
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unexpected broad capital public schema")
    _require(payload.get("product_id") == PRODUCT_ID, "unexpected broad capital product id")
    _require(payload.get("scope") == SCOPE, "unexpected broad capital scope")
    ready = payload.get("same_day_operational_readiness") or {}
    if payload.get("status") == STATUS_READY:
        _require(ready.get("ready") is True, "ready status/readiness mismatch")
        _require((payload.get("turnover") or {}).get("complete_through_market_date") is True, "turnover incomplete")
        _require(int((payload.get("financing") or {}).get("publication_lag_trading_days", 99)) <= 1, "financing lag too stale")
        _require(int((payload.get("financing") or {}).get("older_missing_date_count", 99)) == 0, "financing historical gap")
    _require(ready.get("full_same_observation_date_claimed") is False, "same-date overclaim")
    _require(ready.get("no_forward_fill") is True, "forward fill forbidden")
    authority = payload.get("authority") or {}
    for key in (
        "contains_model_output", "contains_private_thresholds", "contains_private_evidence",
        "forward_outcomes_read", "evidence_qualification_changed", "production_permission_changed", "trading_authority",
    ):
        _require(authority.get(key) is False, f"public broad capital authority drift: {key}")


def write_bundle_manifest(root: Path, relative_paths: list[str], output_path: Path, *, market_date: str) -> dict[str, Any]:
    files=[]
    for rel in relative_paths:
        path=root/rel
        _require(path.is_file(), f"bundle member missing: {rel}")
        files.append({"path":rel,"sha256":sha256_path(path),"size_bytes":path.stat().st_size})
    payload={
        "bundle_id": f"MARKET_REGIME_BROAD_MARKET_CAPITAL_V0_{market_date.replace('-', '_')}",
        "market_date": market_date,
        "public_only": True,
        "contains_model_output": False,
        "contains_private_thresholds": False,
        "contains_private_evidence": False,
        "forward_outcomes_read": False,
        "production_permission_changed": False,
        "trading_authority": False,
        "files": files,
    }
    output_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return payload


__all__=[
    "PRODUCT_ID","SCHEMA_VERSION","SCOPE","STATUS_INSUFFICIENT","STATUS_READY",
    "append_exact_history","build_summary","normalize_financing","normalize_turnover",
    "sha256_path","validate_summary","write_bundle_manifest",
]
