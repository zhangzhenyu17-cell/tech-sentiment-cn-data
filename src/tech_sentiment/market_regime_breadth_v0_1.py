from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

SCHEMA_VERSION = "market-regime-v0-1-direct-security-breadth-v1"
STATUS_QUALIFIED = "QUALIFIED_DIRECT_SECURITY_LEVEL_TREND_BREADTH"
STATUS_PARTIAL = "PARTIAL_DIRECT_SECURITY_LEVEL_TREND_BREADTH"
UNIVERSE_SCOPE = "SSE_SZSE_A_SHARE_SINA_LIVE_UNIVERSE"
HISTORY_SOURCE = "TENCENT_DIRECT_UNADJUSTED_DAILY_CLOSE"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def normalize_snapshot(frame: pd.DataFrame, *, market_date: str) -> pd.DataFrame:
    required = {"symbol", "name", "latest", "prev_close", "timestamp", "market_prefix"}
    missing = required - set(frame.columns)
    _require(not missing, f"snapshot missing columns: {sorted(missing)}")
    out = frame.copy()
    out["symbol"] = out["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
    out["market_prefix"] = out["market_prefix"].astype(str).str.lower()
    _require(out["market_prefix"].isin({"sh", "sz"}).all(), "snapshot must be SSE/SZSE only")
    _require(out["symbol"].notna().all(), "invalid snapshot symbol")
    _require(not out["symbol"].duplicated().any(), "duplicate snapshot symbol")
    for col in ("latest", "prev_close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    _require(out["prev_close"].notna().all() and (out["prev_close"] > 0).all(), "invalid snapshot prev_close")
    _require(out["latest"].notna().all() and (out["latest"] >= 0).all(), "invalid snapshot latest")
    out["spot_price_valid"] = out["latest"] > 0
    out["market_date"] = str(pd.Timestamp(market_date).date())
    return out.sort_values("symbol").reset_index(drop=True)


def compute_direct_security_breadth(
    snapshot: pd.DataFrame,
    history: pd.DataFrame,
    *,
    market_date: str,
    query_coverage: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    snap = normalize_snapshot(snapshot, market_date=market_date)
    required = {"date", "symbol", "close", "provider"}
    missing = required - set(history.columns)
    _require(not missing, f"history missing columns: {sorted(missing)}")
    prices = history.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
    prices["symbol"] = prices["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    _require(prices["symbol"].notna().all(), "invalid history symbol")
    _require(prices["close"].notna().all() and (prices["close"] > 0).all(), "invalid history close")
    _require(not prices.duplicated(["symbol", "date"]).any(), "duplicate history symbol/date")
    date = pd.Timestamp(market_date).normalize()
    _require(not (prices["date"] > date).any(), "future history row detected")
    wanted = set(snap["symbol"])
    _require(set(prices["symbol"]).issubset(wanted), "history includes symbol outside snapshot universe")
    prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)

    g = prices.groupby("symbol", group_keys=False)
    prices["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    prices["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=30).mean())
    prices["low60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=30).min())
    prices["high252"] = g["close"].transform(lambda s: s.rolling(252, min_periods=100).max())
    prices["pct_chg"] = g["close"].pct_change(fill_method=None) * 100.0

    latest = prices.loc[prices["date"].eq(date)].copy()
    latest = snap.merge(
        latest[["symbol", "close", "pct_chg", "ma20", "ma60", "low60", "high252", "provider"]],
        on="symbol",
        how="left",
        validate="one_to_one",
    )
    latest["same_day_history_available"] = latest["close"].notna()
    latest["spot_close_matches_history"] = (
        latest["spot_price_valid"]
        & latest["same_day_history_available"]
        & ((latest["latest"] - latest["close"]).abs() <= 0.011)
    )
    latest["is_advance"] = latest["pct_chg"].gt(0).where(latest["pct_chg"].notna())
    latest["is_decline"] = latest["pct_chg"].lt(0).where(latest["pct_chg"].notna())
    latest["is_flat"] = latest["pct_chg"].eq(0).where(latest["pct_chg"].notna())
    latest["above_ma20"] = latest["close"].gt(latest["ma20"]).where(latest["ma20"].notna())
    latest["above_ma60"] = latest["close"].gt(latest["ma60"]).where(latest["ma60"].notna())
    latest["new_low_60"] = (
        latest["close"].le(latest["low60"] * 1.001).where(latest["low60"].notna())
    )
    latest["new_high_252"] = (
        latest["close"].ge(latest["high252"] * 0.999).where(latest["high252"].notna())
    )

    def ratio(col: str) -> tuple[int, float | None]:
        valid = latest[col].notna()
        n = int(valid.sum())
        return n, (float(latest.loc[valid, col].astype(float).mean()) if n else None)

    advance_n, advance_ratio = ratio("is_advance")
    ma20_n, ma20_ratio = ratio("above_ma20")
    ma60_n, ma60_ratio = ratio("above_ma60")
    low60_n, low60_ratio = ratio("new_low_60")
    high252_n, high252_ratio = ratio("new_high_252")
    same_day_n = int(latest["same_day_history_available"].sum())
    spot_valid_n = int(latest["spot_price_valid"].sum())
    spot_match_n = int(latest["spot_close_matches_history"].sum())
    query_symbol_count = int(query_coverage.get("query_symbol_count", -1))
    complete_query_count = int(query_coverage.get("complete_query_count", -1))
    query_error_count = int(query_coverage.get("query_error_count", -1))
    query_complete = (
        query_symbol_count == len(snap)
        and complete_query_count == len(snap)
        and query_error_count == 0
    )
    metrics_present = all(n > 0 for n in (advance_n, ma20_n, ma60_n, low60_n, high252_n))
    qualified = bool(query_complete and same_day_n > 0 and metrics_present)

    same_day_return = pd.to_numeric(latest.loc[latest["pct_chg"].notna(), "pct_chg"], errors="coerce")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_QUALIFIED if qualified else STATUS_PARTIAL,
        "market_date": str(date.date()),
        "universe_scope": UNIVERSE_SCOPE,
        "universe_security_count": int(len(snap)),
        "same_day_history_security_count": same_day_n,
        "same_day_history_coverage_ratio": same_day_n / len(snap) if len(snap) else 0.0,
        "spot_price_valid_security_count": spot_valid_n,
        "spot_history_close_match_count": spot_match_n,
        "spot_history_close_match_ratio_of_valid_spot_and_same_day": spot_match_n / min(spot_valid_n, same_day_n) if min(spot_valid_n, same_day_n) else 0.0,
        "query_symbol_count": query_symbol_count,
        "complete_query_count": complete_query_count,
        "query_error_count": query_error_count,
        "direct_security_level_raw_snapshot_materialized": True,
        "qualified_trend_breadth": qualified,
        "breadth": {
            "advance_ratio": advance_ratio,
            "above_ma20_ratio": ma20_ratio,
            "above_ma60_ratio": ma60_ratio,
            "new_low_60_ratio": low60_ratio,
            "new_high_252_ratio": high252_ratio,
            "equal_weight_return_pct": float(same_day_return.mean()) if len(same_day_return) else None,
            "advancing_security_count": int(latest["is_advance"].eq(True).sum()),
            "declining_security_count": int(latest["is_decline"].eq(True).sum()),
            "flat_security_count": int(latest["is_flat"].eq(True).sum()),
        },
        "metric_denominators": {
            "advance_ratio": advance_n,
            "above_ma20_ratio": ma20_n,
            "above_ma60_ratio": ma60_n,
            "new_low_60_ratio": low60_n,
            "new_high_252_ratio": high252_n,
        },
        "rolling_semantics": {
            "ma20": "rolling20_close_min_periods_10",
            "ma60": "rolling60_close_min_periods_30",
            "new_low_60": "close_lte_rolling60_close_low_times_1.001_min_periods_30",
            "new_high_252": "close_gte_rolling252_close_high_times_0.999_min_periods_100",
            "warmup_missing_excluded_from_metric_denominator": True,
        },
        "source": {
            "universe_snapshot": "SINA_STOCK_ZH_A_SPOT_FILTERED_TO_SH_SZ",
            "history": HISTORY_SOURCE,
            "price_adjustment": "NONE",
        },
        "scope_limitations": {
            "beijing_stock_exchange_in_direct_trend_breadth": False,
            "beijing_exclusion_reason": "CURRENT_HISTORY_PROVIDER_UNSUPPORTED",
            "full_sh_sz_bj_market_breadth_claimed": False,
            "current_snapshot_is_historical_membership": False,
        },
        "authority": {
            "contains_model_output": False,
            "state_rewrite_allowed": False,
            "threshold_change_allowed": False,
            "future_transition_probability_estimated": False,
            "forward_outcomes_read": False,
            "production_permission_changed": False,
            "trading_authority": False,
        },
    }
    validate_direct_security_breadth(summary)
    return latest.sort_values("symbol").reset_index(drop=True), summary


def validate_direct_security_breadth(payload: Mapping[str, Any]) -> None:
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unexpected breadth schema")
    _require(payload.get("universe_scope") == UNIVERSE_SCOPE, "unexpected breadth universe")
    _require(payload.get("direct_security_level_raw_snapshot_materialized") is True, "direct raw snapshot missing")
    _require(payload.get("query_error_count", -1) >= 0, "invalid query error count")
    breadth = payload.get("breadth")
    _require(isinstance(breadth, Mapping), "breadth metrics missing")
    denominators = payload.get("metric_denominators")
    _require(isinstance(denominators, Mapping), "breadth denominators missing")
    for key in ("advance_ratio", "above_ma20_ratio", "above_ma60_ratio", "new_low_60_ratio", "new_high_252_ratio"):
        value = breadth.get(key)
        _require(value is None or 0.0 <= float(value) <= 1.0, f"invalid breadth ratio: {key}")
        _require(int(denominators.get(key, -1)) >= 0, f"invalid breadth denominator: {key}")
    if payload.get("qualified_trend_breadth") is True:
        _require(payload.get("status") == STATUS_QUALIFIED, "qualified breadth status mismatch")
        _require(int(payload.get("query_error_count")) == 0, "qualified breadth has query errors")
        _require(int(payload.get("complete_query_count")) == int(payload.get("query_symbol_count")), "qualified breadth incomplete queries")
        _require(all(int(denominators[k]) > 0 for k in denominators), "qualified breadth has empty denominator")
    limits = payload.get("scope_limitations")
    _require(isinstance(limits, Mapping), "scope limitations missing")
    _require(limits.get("beijing_stock_exchange_in_direct_trend_breadth") is False, "BSE scope drift")
    _require(limits.get("full_sh_sz_bj_market_breadth_claimed") is False, "full-market overclaim")
    authority = payload.get("authority")
    _require(isinstance(authority, Mapping), "breadth authority missing")
    _require(authority.get("contains_model_output") is False, "breadth cannot contain model output")
    for key in (
        "state_rewrite_allowed", "threshold_change_allowed", "future_transition_probability_estimated",
        "forward_outcomes_read", "production_permission_changed", "trading_authority",
    ):
        _require(authority.get(key) is False, f"breadth authority escalation: {key}")


def write_bundle_manifest(root: Path, *, relative_paths: list[str], output_path: Path, market_date: str) -> dict[str, Any]:
    files = []
    for rel in relative_paths:
        path = root / rel
        _require(path.is_file(), f"bundle member missing: {rel}")
        files.append({"path": rel, "sha256": sha256_path(path), "size_bytes": path.stat().st_size})
    manifest = {
        "bundle_id": f"MARKET_REGIME_V0_1_DIRECT_BREADTH_{market_date.replace('-', '_')}",
        "market_date": market_date,
        "public_only": True,
        "contains_model_output": False,
        "contains_private_evidence": False,
        "forward_outcomes_read": False,
        "production_permission_changed": False,
        "trading_authority": False,
        "files": files,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


__all__ = [
    "HISTORY_SOURCE", "SCHEMA_VERSION", "STATUS_PARTIAL", "STATUS_QUALIFIED", "UNIVERSE_SCOPE",
    "compute_direct_security_breadth", "normalize_snapshot", "sha256_path", "validate_direct_security_breadth",
    "write_bundle_manifest",
]
