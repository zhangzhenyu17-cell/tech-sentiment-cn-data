from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from .universe import normalize_symbol, universe_diagnostics


BUNDLE_KIND = "sector_design_public_input_v1"
INNOVATION_DRUG_INDEX = "931152"
INNOVATION_DRUG_DESIGN_START = pd.Timestamp("2019-04-22")
INNOVATION_DRUG_DESIGN_END = pd.Timestamp("2023-12-31")
INNOVATION_DRUG_WARMUP_START = pd.Timestamp("2018-01-01")
TECHNICAL_PRICE_ADJUSTMENT = "qfq"
PCT_CHG_SEMANTICS = "qfq_close_to_close"
MIN_LIMIT_COVERAGE = 0.95


@dataclass(frozen=True)
class SectorDesignBundleAudit:
    eligible: bool
    errors: tuple[str, ...]
    symbols: int
    universe_intervals: int
    price_rows: int
    index_rows: int
    active_design_price_rows: int
    manifest: dict[str, object]


def file_sha256(path: str | Path) -> str:
    target = Path(path)
    digest = sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_qfq_design_price_semantics(prices: pd.DataFrame) -> pd.DataFrame:
    """Normalize an already-QFQ stock history for the sector design bundle.

    `close` is assumed to come from a provider request explicitly made with
    qfq/front-adjustment. Daily percentage change is recomputed from that same
    close series so rolling price features and daily-return features share one
    corporate-action-adjusted price rail. The function is outcome-blind.
    """

    required = {"date", "symbol", "close", "amount"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"QFQ price history missing columns: {sorted(missing)}")

    out = prices.copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    if out.duplicated(subset=["date", "symbol"], keep=False).any():
        raise ValueError("duplicate QFQ stock-day rows")
    if out[["close", "amount"]].isna().any().any():
        raise ValueError("QFQ close/amount must be numeric and non-missing")
    if (out["close"] <= 0).any() or (out["amount"] < 0).any():
        raise ValueError("QFQ close must be positive and amount non-negative")

    out = out.sort_values(["symbol", "date"]).reset_index(drop=True)
    out["pct_chg"] = (
        out.groupby("symbol", sort=False)["close"]
        .pct_change(fill_method=None)
        .mul(100.0)
    )
    out["technical_price_adjustment"] = TECHNICAL_PRICE_ADJUSTMENT
    out["pct_chg_semantics"] = PCT_CHG_SEMANTICS
    return out


def attach_limit_qualification(
    qfq_prices: pd.DataFrame,
    limit_rows: pd.DataFrame,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """Attach qualified stock-day limit semantics without contaminating warm-up rows.

    Limit semantics are required only for active design member-days. Warm-up and
    out-of-universe rows remain usable for rolling technical calculations but are
    explicitly ineligible for extreme/price-limit features.
    """

    required_limit = {
        "date",
        "symbol",
        "limit_pct",
        "limit_eligible",
        "limit_rule_source",
    }
    missing = required_limit - set(limit_rows.columns)
    if missing:
        raise ValueError(f"limit rows missing columns: {sorted(missing)}")

    prices = qfq_prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
    prices["symbol"] = prices["symbol"].map(normalize_symbol)

    limits = limit_rows.loc[:, sorted(required_limit)].copy()
    limits["date"] = pd.to_datetime(limits["date"], errors="raise").dt.normalize()
    limits["symbol"] = limits["symbol"].map(normalize_symbol)
    if limits.duplicated(subset=["date", "symbol"], keep=False).any():
        raise ValueError("duplicate qualified limit rows")

    merged = prices.merge(
        limits,
        on=["date", "symbol"],
        how="left",
        validate="one_to_one",
    )
    merged["limit_eligible"] = merged["limit_eligible"].fillna(False).astype(bool)
    merged["limit_rule_source"] = (
        merged["limit_rule_source"]
        .fillna("not_active_member_limit_not_required")
        .astype(str)
    )

    uni = universe.copy()
    uni["effective_start"] = pd.to_datetime(uni["effective_start"], errors="raise").dt.normalize()
    uni["effective_end"] = pd.to_datetime(uni["effective_end"], errors="raise").dt.normalize()
    uni["symbol"] = uni["symbol"].map(normalize_symbol)
    membership = merged[["date", "symbol"]].merge(
        uni[["symbol", "effective_start", "effective_end"]],
        on="symbol",
        how="left",
    )
    active = (
        membership["effective_start"].notna()
        & (membership["date"] >= membership["effective_start"])
        & (membership["date"] <= membership["effective_end"])
    )
    # A symbol can have multiple non-overlapping membership intervals. Collapse
    # the expanded merge back to the original price key.
    active_by_key = (
        membership.assign(_active=active)
        .groupby(["date", "symbol"], as_index=False)["_active"]
        .max()
    )
    merged = merged.merge(active_by_key, on=["date", "symbol"], how="left", validate="one_to_one")
    active_mask = merged["_active"].fillna(False).astype(bool)
    missing_limit = active_mask & merged["limit_rule_source"].eq("not_active_member_limit_not_required")
    if missing_limit.any():
        raise ValueError(
            f"qualified design bundle is missing {int(missing_limit.sum())} active member-day limit rows"
        )
    return merged.drop(columns=["_active"])


def _truthy_false(value: object) -> bool:
    if isinstance(value, bool):
        return value is False
    return str(value).strip().lower() in {"false", "0", "no"}


def audit_innovation_drug_design_bundle(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    index_prices: pd.DataFrame,
    manifest: Mapping[str, object],
) -> SectorDesignBundleAudit:
    """Fail closed unless a public 931152 bundle is design-only and fully qualified."""

    errors: list[str] = []
    expected_manifest = {
        "bundle_kind": BUNDLE_KIND,
        "index_code": INNOVATION_DRUG_INDEX,
        "design_start": "2019-04-22",
        "design_end": "2023-12-31",
        "warmup_start": "2018-01-01",
        "technical_price_adjustment": TECHNICAL_PRICE_ADJUSTMENT,
        "pct_chg_semantics": PCT_CHG_SEMANTICS,
        "membership_gate": "eligible",
        "limit_gate": "eligible",
        "qualification_scope": "historical_design_research_input_only",
    }
    for key, expected in expected_manifest.items():
        if str(manifest.get(key, "")) != expected:
            errors.append(f"manifest {key} must equal {expected!r}")
    try:
        coverage = float(manifest.get("minimum_daily_limit_coverage", "nan"))
    except (TypeError, ValueError):
        coverage = float("nan")
    if pd.isna(coverage) or coverage < MIN_LIMIT_COVERAGE:
        errors.append("manifest minimum_daily_limit_coverage must be >= 0.95")
    if not _truthy_false(manifest.get("holdout_included")):
        errors.append("manifest holdout_included must be false")
    if not _truthy_false(manifest.get("model_outcomes_included")):
        errors.append("manifest model_outcomes_included must be false")

    uni = universe.copy()
    diagnostics = universe_diagnostics(uni)
    if not diagnostics.has_point_in_time_history:
        errors.append("universe must be genuine point-in-time history")
    for column in ("effective_start", "effective_end"):
        if column not in uni.columns:
            errors.append(f"universe missing {column}")
        else:
            uni[column] = pd.to_datetime(uni[column], errors="coerce").dt.normalize()
    if {"effective_start", "effective_end"}.issubset(uni.columns):
        if uni[["effective_start", "effective_end"]].isna().any().any():
            errors.append("universe ranges must be bounded and valid")
        else:
            if uni["effective_start"].min() < INNOVATION_DRUG_DESIGN_START:
                errors.append("universe starts before frozen design start")
            if uni["effective_end"].max() > INNOVATION_DRUG_DESIGN_END:
                errors.append("universe crosses frozen design end")

    required_prices = {
        "date",
        "symbol",
        "close",
        "pct_chg",
        "amount",
        "limit_pct",
        "limit_eligible",
        "limit_rule_source",
        "technical_price_adjustment",
        "pct_chg_semantics",
    }
    missing_price_columns = required_prices - set(prices.columns)
    if missing_price_columns:
        errors.append(f"prices missing columns: {sorted(missing_price_columns)}")
    stock_symbols = 0
    active_design_rows = 0
    if not missing_price_columns:
        px = prices.copy()
        px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.normalize()
        px["symbol"] = px["symbol"].map(normalize_symbol)
        if px["date"].isna().any():
            errors.append("prices contain invalid dates")
        else:
            if px["date"].min() > INNOVATION_DRUG_WARMUP_START:
                errors.append("prices do not reach frozen warm-up start")
            if px["date"].max() > INNOVATION_DRUG_DESIGN_END:
                errors.append("prices contain post-design/holdout rows")
        if set(px["technical_price_adjustment"].astype(str)) != {TECHNICAL_PRICE_ADJUSTMENT}:
            errors.append("prices must use qfq technical price semantics only")
        if set(px["pct_chg_semantics"].astype(str)) != {PCT_CHG_SEMANTICS}:
            errors.append("prices must use qfq_close_to_close pct_chg semantics only")
        if px.duplicated(subset=["date", "symbol"], keep=False).any():
            errors.append("prices contain duplicate stock-day rows")
        stock_symbols = int(px["symbol"].nunique())
        design_px = px[px["date"].between(INNOVATION_DRUG_DESIGN_START, INNOVATION_DRUG_DESIGN_END)]
        active_design_rows = int(len(design_px))

    required_index = {"date", "index_code", "close"}
    missing_index = required_index - set(index_prices.columns)
    index_rows = 0
    if missing_index:
        errors.append(f"index_prices missing columns: {sorted(missing_index)}")
    else:
        idx = index_prices.copy()
        idx["date"] = pd.to_datetime(idx["date"], errors="coerce").dt.normalize()
        if set(idx["index_code"].astype(str).str.zfill(6)) != {INNOVATION_DRUG_INDEX}:
            errors.append("index_prices must contain only 931152")
        if idx["date"].isna().any():
            errors.append("index_prices contain invalid dates")
        else:
            if idx["date"].min() > INNOVATION_DRUG_DESIGN_START:
                errors.append("index price rail starts after design start")
            if idx["date"].max() > INNOVATION_DRUG_DESIGN_END:
                errors.append("index price rail contains holdout rows")
        if idx.duplicated(subset=["date"], keep=False).any():
            errors.append("index_prices contain duplicate dates")
        index_rows = int(len(idx))

    return SectorDesignBundleAudit(
        eligible=not errors,
        errors=tuple(errors),
        symbols=stock_symbols,
        universe_intervals=int(len(universe)),
        price_rows=int(len(prices)),
        index_rows=index_rows,
        active_design_price_rows=active_design_rows,
        manifest=dict(manifest),
    )


def read_manifest(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("design bundle manifest must be a JSON object")
    return payload
