from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.filing_materialization import (
    FILING_MATERIALIZER_VERSION,
    materialize_versioned_filing_facts,
)
from tech_sentiment.fundamental_pit_state import (
    CONTRACT_ID as FUNDAMENTAL_STATE_CONTRACT_ID,
    FORMULA_VERSION as FUNDAMENTAL_STATE_FORMULA_VERSION,
    materialize_fundamental_state_evidence,
)
from tech_sentiment.official_filing_facts import FILING_PARSER_VERSION
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records

SCHEMA_VERSION = "cross-sector-fundamental-expansion-shard-b-work-unit-v1"
SHARD_ID = "B"
TARGET_START_DATE = "2026-01-01"
DECISION_DATE = "2026-09-29"
WARMUP_YEARS = 2
QUALIFIED = "HISTORICAL_RECONSTRUCTABLE"


def _entity_id(symbol: str) -> str:
    code = str(symbol).zfill(6)
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_scope(path: Path, contract_path: Path) -> pd.DataFrame:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("shard_id") != SHARD_ID or contract.get("shard_definition") != "sorted(M)[1::2]":
        raise ValueError("Shard B scope contract identity drift")
    if contract.get("scope_csv_path") != path.as_posix():
        raise ValueError("Shard B scope path drift")
    if _sha256(path) != contract.get("scope_csv_sha256"):
        raise ValueError("Shard B scope checksum drift")
    frame = pd.read_csv(path, dtype=str)
    required = {
        "entity_id",
        "symbol",
        "exchange",
        "in_innovation_drug",
        "in_defense",
        "in_core_beta",
        "shard_id",
        "scope_decision_date",
        "source_membership_identity",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"scope CSV missing columns: {sorted(missing)}")
    if len(frame) != 218:
        raise ValueError(f"Shard B scope must contain 218 entities, got {len(frame)}")
    if set(frame["shard_id"]) != {SHARD_ID}:
        raise ValueError("scope shard_id drift")
    if set(frame["scope_decision_date"]) != {"2026-09-30"}:
        raise ValueError("scope decision date drift")
    expected = frame["symbol"].astype(str).str.zfill(6).map(_entity_id)
    if not expected.equals(frame["entity_id"].astype(str)):
        raise ValueError("entity_id mapping drift")
    if frame["entity_id"].duplicated().any():
        raise ValueError("duplicate entity_id in Shard B scope")
    for col in ("in_innovation_drug", "in_defense", "in_core_beta"):
        values = set(frame[col].astype(str).str.lower())
        if not values.issubset({"true", "false"}):
            raise ValueError(f"invalid domain routing values in {col}: {sorted(values)}")
    if not frame["source_membership_identity"].astype(str).str.startswith(
        "CSI_OFFICIAL_CONSTITUENT_XLS_2026-09-30::"
    ).all():
        raise ValueError("source membership identity drift")
    return frame.sort_values("entity_id").reset_index(drop=True)


def _work_unit(frame: pd.DataFrame, *, index: int, count: int) -> pd.DataFrame:
    if count < 1 or index < 0 or index >= count:
        raise ValueError("invalid work-unit index/count")
    return frame.iloc[[i for i in range(len(frame)) if i % count == index]].copy()


def _canonicalize(records: pd.DataFrame) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame() if records is None else records.copy()
    return validate_materialized_pit_records(records.copy())


def _ready_entities(evidence: pd.DataFrame) -> set[str]:
    if evidence is None or evidence.empty:
        return set()
    needed = {"entity_id", "availability_state", "evidence_available_date"}
    if not needed.issubset(evidence.columns):
        return set()
    work = evidence.copy()
    work["evidence_available_date"] = pd.to_datetime(work["evidence_available_date"], errors="coerce")
    work = work[
        work["availability_state"].astype(str).eq(QUALIFIED)
        & (work["evidence_available_date"] <= pd.Timestamp(DECISION_DATE))
    ]
    return set(work["entity_id"].astype(str))


def _error_reason(errors: pd.DataFrame, symbol: str, entity_id: str) -> str | None:
    if errors is None or errors.empty:
        return None
    subset = errors
    if "entity_id" in subset.columns:
        hit = subset[subset["entity_id"].astype(str).eq(entity_id)]
        if not hit.empty:
            subset = hit
        elif "symbol" in subset.columns:
            subset = subset[subset["symbol"].astype(str).str.zfill(6).eq(symbol)]
        else:
            return None
    elif "symbol" in subset.columns:
        subset = subset[subset["symbol"].astype(str).str.zfill(6).eq(symbol)]
    else:
        return None
    if subset.empty:
        return None
    for col in ("failure_reason", "reason", "error", "message", "detail"):
        if col in subset.columns:
            vals = [str(x).strip() for x in subset[col].dropna().tolist() if str(x).strip()]
            if vals:
                return f"SOURCE_OR_PARSER_ERROR:{vals[0][:300]}"
    return "SOURCE_OR_PARSER_ERROR:DETAIL_UNAVAILABLE"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize one outcome-blind work unit for CSRM Fundamental Expansion Shard B."
    )
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--scope-contract", type=Path, required=True)
    parser.add_argument("--calendar-csv", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--work-unit-index", type=int, required=True)
    parser.add_argument("--work-unit-count", type=int, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    scope = _read_scope(args.scope_csv, args.scope_contract)
    unit = _work_unit(scope, index=args.work_unit_index, count=args.work_unit_count)
    if unit.empty:
        raise SystemExit("Shard B work unit has no entities")
    symbols = unit["symbol"].astype(str).str.zfill(6).tolist()

    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()
    if trading_dates.max() > pd.Timestamp(DECISION_DATE):
        trading_dates = trading_dates[trading_dates <= pd.Timestamp(DECISION_DATE)]

    filings = materialize_versioned_filing_facts(
        symbols,
        target_start_date=TARGET_START_DATE,
        end_date=DECISION_DATE,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir / "filings",
        warmup_years=WARMUP_YEARS,
    )
    fundamental = materialize_fundamental_state_evidence(
        filings.facts,
        target_start_date=TARGET_START_DATE,
        target_end_date=DECISION_DATE,
    )
    evidence = _canonicalize(fundamental.evidence)
    ready = _ready_entities(evidence)

    receipts = []
    for row in unit.to_dict("records"):
        entity_id = str(row["entity_id"])
        symbol = str(row["symbol"]).zfill(6)
        if entity_id in ready:
            state = "READY"
            reason = ""
        else:
            state = "MISSING"
            reason = _error_reason(filings.errors, symbol, entity_id) or (
                f"NO_HISTORICAL_RECONSTRUCTABLE_FUNDAMENTAL_STATE_BY_{DECISION_DATE}"
            )
        receipts.append(
            {
                "entity_id": entity_id,
                "symbol": symbol,
                "readiness": state,
                "failure_reason": reason,
                "work_unit_index": args.work_unit_index,
                "work_unit_count": args.work_unit_count,
            }
        )

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    filings.facts.to_csv(out / "versioned_filing_facts.csv", index=False)
    evidence.to_csv(out / "fundamental_state_evidence.csv", index=False)
    fundamental.coverage.to_csv(out / "fundamental_state_coverage.csv", index=False)
    filings.coverage.to_csv(out / "filing_coverage.csv", index=False)
    filings.errors.to_csv(out / "filing_errors.csv", index=False)
    pd.DataFrame(receipts).to_csv(out / "entity_readiness.csv", index=False)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "parent_shard_id": SHARD_ID,
        "source_commit": args.source_commit,
        "scope_csv_sha256": _sha256(args.scope_csv),
        "scope_contract_sha256": _sha256(args.scope_contract),
        "target_start_date": TARGET_START_DATE,
        "decision_date": DECISION_DATE,
        "warmup_years": WARMUP_YEARS,
        "work_unit_index": args.work_unit_index,
        "work_unit_count": args.work_unit_count,
        "symbols": symbols,
        "entity_ids": unit["entity_id"].astype(str).tolist(),
        "entity_count": len(unit),
        "ready_count": sum(x["readiness"] == "READY" for x in receipts),
        "missing_count": sum(x["readiness"] != "READY" for x in receipts),
        "filing_materialization": filings.summary,
        "fundamental_state_contract": fundamental.summary,
        "parser_identity": f"tech_sentiment.filing_materialization@{args.source_commit}",
        "filing_materializer_version": FILING_MATERIALIZER_VERSION,
        "filing_parser_version": FILING_PARSER_VERSION,
        "state_builder_identity": f"tech_sentiment.fundamental_pit_state@{args.source_commit}",
        "fundamental_state_contract_id": FUNDAMENTAL_STATE_CONTRACT_ID,
        "fundamental_state_formula_version": FUNDAMENTAL_STATE_FORMULA_VERSION,
        "outcome_read": False,
        "historical_outcome_read": False,
        "prospective_outcome_read": False,
        "parameter_search_run": False,
        "threshold_search_run": False,
        "weight_search_run": False,
        "ml_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
