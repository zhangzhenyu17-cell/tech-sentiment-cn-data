from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = "cross-sector-current-fundamental-aggregate-v1"
QUALIFIED = "HISTORICAL_RECONSTRUCTABLE"


def _entity_id(symbol: str) -> str:
    code = str(symbol).zfill(6)
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def _read_many(root: Path, name: str) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob(name)):
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        if not frame.empty:
            frame = frame.copy()
            frame["_source_file"] = path.as_posix()
            frames.append(frame)
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate cross-sector current Fundamental shard outputs without outcome access."
    )
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--scope-label", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    scope = pd.read_csv(args.scope_csv, dtype={"symbol": str})
    if "symbol" not in scope.columns:
        raise ValueError("scope CSV missing symbol")
    expected_symbols = sorted({str(x).zfill(6) for x in scope["symbol"].dropna()})
    expected_entities = {_entity_id(x) for x in expected_symbols}

    evidence_parts = _read_many(args.shard_root, "fundamental_state_evidence.csv")
    coverage_parts = _read_many(args.shard_root, "fundamental_state_coverage.csv")
    filing_coverage_parts = _read_many(args.shard_root, "filing_coverage.csv")
    error_parts = _read_many(args.shard_root, "filing_errors.csv")
    manifest_paths = sorted(args.shard_root.rglob("stage_manifest.json"))

    evidence = pd.concat(evidence_parts, ignore_index=True) if evidence_parts else pd.DataFrame()
    if not evidence.empty:
        if "evidence_id" not in evidence.columns:
            raise ValueError("fundamental evidence missing evidence_id")
        forbidden = [
            c for c in evidence.columns
            if any(token in str(c).lower() for token in ("forward_return", "outcome", "mfe", "mae"))
        ]
        if forbidden:
            raise ValueError(f"outcome-like columns forbidden: {forbidden}")
        evidence = evidence.drop_duplicates("evidence_id").sort_values(
            ["entity_id", "evidence_available_date", "event_date"]
        )
    coverage = pd.concat(coverage_parts, ignore_index=True) if coverage_parts else pd.DataFrame()
    filing_coverage = pd.concat(filing_coverage_parts, ignore_index=True) if filing_coverage_parts else pd.DataFrame()
    errors = pd.concat(error_parts, ignore_index=True) if error_parts else pd.DataFrame()

    observed_symbols: set[str] = set()
    manifests: list[dict] = []
    for path in manifest_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema_version") != "cross-sector-current-fundamental-shard-v1":
            continue
        if item.get("outcome_read") is not False:
            raise ValueError("shard outcome boundary drift")
        observed_symbols.update(str(x).zfill(6) for x in item.get("symbols", []))
        manifests.append(item)

    if observed_symbols != set(expected_symbols):
        missing = sorted(set(expected_symbols) - observed_symbols)
        extra = sorted(observed_symbols - set(expected_symbols))
        raise ValueError(f"aggregate shard scope mismatch: missing={missing[:10]} extra={extra[:10]}")

    latest_qualified_entities: set[str] = set()
    if not evidence.empty:
        e = evidence.copy()
        e["evidence_available_date"] = pd.to_datetime(e["evidence_available_date"], errors="raise")
        e = e[
            e["entity_id"].astype(str).isin(expected_entities)
            & e["availability_state"].astype(str).eq(QUALIFIED)
            & (e["evidence_available_date"] <= pd.Timestamp("2026-09-30"))
        ]
        if not e.empty:
            latest = e.sort_values(["entity_id", "evidence_available_date", "event_date"]).groupby("entity_id").tail(1)
            latest_qualified_entities = set(latest["entity_id"].astype(str))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence.to_csv(args.out_dir / "combined_fundamental_state_evidence.csv", index=False)
    coverage.to_csv(args.out_dir / "combined_fundamental_state_coverage.csv", index=False)
    filing_coverage.to_csv(args.out_dir / "combined_filing_coverage.csv", index=False)
    errors.to_csv(args.out_dir / "combined_filing_errors.csv", index=False)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "scope_label": args.scope_label,
        "scope_symbols": len(expected_symbols),
        "observed_shard_symbols": len(observed_symbols),
        "shard_manifests": len(manifests),
        "qualified_current_entities": len(latest_qualified_entities),
        "qualified_current_coverage": (
            len(latest_qualified_entities) / len(expected_symbols) if expected_symbols else 0.0
        ),
        "remaining_data_insufficient_entities": len(expected_entities - latest_qualified_entities),
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
        "public_materialization_grants_private_pairwise_qualification": False,
    }
    (args.out_dir / "aggregate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
