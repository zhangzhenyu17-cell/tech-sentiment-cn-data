from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


AUDIT_VERSION = "fundamental-extended-pit-coverage-audit-v1"
REQUIRED_PROVENANCE = (
    "entity_id",
    "period_end",
    "fact_type",
    "value",
    "unit",
    "evidence_available_date",
    "publication_timestamp",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "document_url",
    "document_sha256",
    "parser_version",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_entities(scope: pd.DataFrame) -> list[str]:
    required = {"symbol", "market"}
    missing = required - set(scope.columns)
    if missing:
        raise ValueError(f"scope missing columns: {sorted(missing)}")
    rows: list[str] = []
    for _, row in scope.iterrows():
        symbol = "".join(ch for ch in str(row["symbol"]) if ch.isdigit()).zfill(6)
        market = str(row["market"]).upper().strip()
        if market not in {"SH", "SZ"}:
            raise ValueError(f"unsupported frozen-scope market: {market}:{symbol}")
        rows.append(f"{symbol}.{market}")
    unique = sorted(set(rows))
    if len(unique) != len(scope):
        raise ValueError("scope contains duplicate entity identities")
    return unique


def _read_many(root: Path, filename: str) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    paths = sorted(root.glob(f"**/{filename}"))
    receipts: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    for path in paths:
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        frames.append(frame)
        receipts.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": _sha256(path),
                "rows": int(len(frame)),
            }
        )
    if not frames:
        return pd.DataFrame(), receipts
    nonempty = [frame for frame in frames if len(frame)]
    if not nonempty:
        columns = list(frames[0].columns) if len(frames[0].columns) else []
        return pd.DataFrame(columns=columns), receipts
    return pd.concat(nonempty, ignore_index=True, sort=False), receipts


def _required_provenance_missing(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    missing_columns = [column for column in REQUIRED_PROVENANCE if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"facts missing provenance columns: {missing_columns}")
    mask = pd.Series(False, index=frame.index)
    for column in REQUIRED_PROVENANCE:
        values = frame[column]
        mask |= values.isna() | values.astype(str).str.strip().eq("")
    return int(mask.sum())


def audit_extended_pit_coverage(
    *,
    scope: pd.DataFrame,
    facts: pd.DataFrame,
    coverage: pd.DataFrame,
    errors: pd.DataFrame,
    source_files: Iterable[dict[str, object]] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    expected = _expected_entities(scope)
    expected_set = set(expected)

    if coverage.empty:
        raise ValueError("coverage is empty")
    if "entity_id" not in coverage.columns:
        raise ValueError("coverage missing entity_id")
    coverage = coverage.copy()
    coverage["entity_id"] = coverage["entity_id"].astype(str)
    coverage_counts = coverage["entity_id"].value_counts()
    duplicate_coverage = sorted(coverage_counts[coverage_counts > 1].index.tolist())
    missing_coverage = sorted(expected_set - set(coverage["entity_id"]))
    unexpected_coverage = sorted(set(coverage["entity_id"]) - expected_set)

    hard_errors = 0
    soft_errors = 0
    if not errors.empty:
        if "severity" not in errors.columns:
            raise ValueError("errors missing severity")
        severity = errors["severity"].fillna("").astype(str)
        hard_errors = int(severity.eq("HARD_FAILURE").sum())
        soft_errors = int(severity.eq("SOFT_DATA_INSUFFICIENCY").sum())

    required_provenance_missing_rows = _required_provenance_missing(facts)
    field_rows: list[dict[str, object]] = []
    ambiguity_rows: list[dict[str, object]] = []

    if not facts.empty:
        facts = facts.copy()
        facts["entity_id"] = facts["entity_id"].astype(str)
        unexpected_facts = sorted(set(facts["entity_id"]) - expected_set)
        if unexpected_facts:
            raise ValueError(f"facts contain out-of-scope entities: {unexpected_facts[:20]}")
        facts["period_end"] = pd.to_datetime(facts["period_end"], errors="raise").dt.normalize()
        facts["evidence_available_date"] = pd.to_datetime(
            facts["evidence_available_date"], errors="raise"
        ).dt.normalize()
        facts["publication_timestamp"] = pd.to_datetime(
            facts["publication_timestamp"], errors="raise", utc=True
        )
        facts["value_numeric"] = pd.to_numeric(facts["value"], errors="raise")

        group_keys = [
            "entity_id",
            "period_end",
            "fact_type",
            "evidence_available_date",
            "publication_timestamp",
        ]
        grouped = facts.groupby(group_keys, dropna=False, sort=True)
        for key, group in grouped:
            revisions = sorted(set(group["revision_id"].astype(str)))
            if len(revisions) <= 1:
                continue
            values = sorted(set(float(value) for value in group["value_numeric"]))
            ambiguity_rows.append(
                {
                    "entity_id": key[0],
                    "period_end": str(pd.Timestamp(key[1]).date()),
                    "fact_type": key[2],
                    "evidence_available_date": str(pd.Timestamp(key[3]).date()),
                    "publication_timestamp": pd.Timestamp(key[4]).isoformat(),
                    "revision_count": len(revisions),
                    "value_count": len(values),
                    "value_conflict": len(values) > 1,
                    "revision_ids": ";".join(revisions),
                    "source_native_revision_sequence_qualified": False,
                    "formal_resolution_state": "AMBIGUOUS_NO_SOURCE_NATIVE_SEQUENCE",
                }
            )

        ambiguity = pd.DataFrame(ambiguity_rows)
        for fact_type, group in facts.groupby("fact_type", sort=True):
            entities = sorted(set(group["entity_id"]))
            if ambiguity.empty:
                fact_ambiguity = pd.DataFrame()
            else:
                fact_ambiguity = ambiguity[ambiguity["fact_type"].eq(fact_type)]
            same_timestamp_groups = int(len(fact_ambiguity))
            value_conflicts = (
                int(fact_ambiguity["value_conflict"].astype(bool).sum())
                if len(fact_ambiguity)
                else 0
            )
            units = sorted(set(group["unit"].astype(str)))
            provenance_missing = _required_provenance_missing(group.drop(columns=["value_numeric"]))
            entity_coverage = len(entities) / len(expected) if expected else 0.0
            if len(entities) == len(expected) and same_timestamp_groups == 0 and provenance_missing == 0:
                state = "COMPLETE_ENTITY_COVERAGE_RAW_PIT_NOT_FORMALLY_QUALIFIED"
            elif len(entities) == len(expected) and provenance_missing == 0:
                state = "COMPLETE_ENTITY_COVERAGE_REVISION_SEQUENCE_BLOCKED_NOT_QUALIFIED"
            else:
                state = "PARTIAL_COVERAGE_DATA_INSUFFICIENT_NOT_QUALIFIED"
            field_rows.append(
                {
                    "fact_type": str(fact_type),
                    "rows": int(len(group)),
                    "entities": int(len(entities)),
                    "expected_entities": int(len(expected)),
                    "entity_coverage_ratio": float(entity_coverage),
                    "units": ";".join(units),
                    "required_provenance_missing_rows": int(provenance_missing),
                    "same_timestamp_multi_revision_groups": same_timestamp_groups,
                    "value_conflict_groups": value_conflicts,
                    "source_native_revision_sequence_qualified": False,
                    "formal_state": state,
                }
            )
    else:
        ambiguity = pd.DataFrame(
            columns=[
                "entity_id",
                "period_end",
                "fact_type",
                "evidence_available_date",
                "publication_timestamp",
                "revision_count",
                "value_count",
                "value_conflict",
                "revision_ids",
                "source_native_revision_sequence_qualified",
                "formal_resolution_state",
            ]
        )

    field_audit = pd.DataFrame(field_rows)
    complete_coverage_rows = int(
        coverage["query_status"].astype(str).str.startswith("COMPLETE_WINDOW").sum()
    )
    summary = {
        "audit_version": AUDIT_VERSION,
        "status": (
            "MATERIALIZATION_HARD_FAILURE"
            if hard_errors or missing_coverage or duplicate_coverage or unexpected_coverage
            else "OUTCOME_BLIND_RAW_PIT_COVERAGE_AUDIT_COMPLETE_NOT_QUALIFIED"
        ),
        "scope_version": (
            str(scope.attrs.get("scope_version"))
            if scope.attrs.get("scope_version")
            else "capital-pit-frozen-universe-v2"
        ),
        "expected_entities": len(expected),
        "coverage_entities": int(coverage["entity_id"].nunique()),
        "coverage_complete_window_rows": complete_coverage_rows,
        "missing_coverage_entities": missing_coverage,
        "duplicate_coverage_entities": duplicate_coverage,
        "unexpected_coverage_entities": unexpected_coverage,
        "fact_rows": int(len(facts)),
        "observed_fact_types": (
            sorted(field_audit["fact_type"].astype(str).tolist())
            if len(field_audit)
            else []
        ),
        "field_audit_rows": int(len(field_audit)),
        "revision_ambiguity_groups": int(len(ambiguity)),
        "value_conflict_groups": (
            int(ambiguity["value_conflict"].astype(bool).sum()) if len(ambiguity) else 0
        ),
        "required_provenance_missing_rows": required_provenance_missing_rows,
        "hard_failure_rows": hard_errors,
        "soft_data_insufficiency_rows": soft_errors,
        "source_native_revision_sequence_qualified": False,
        "historical_materialization_run": True,
        "coverage_computed": True,
        "outcome_read": False,
        "historical_outcomes_reread": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "public_audit_may_grant_private_fundamental_qualification": False,
        "next_gate": (
            "PRIVATE_OUTCOME_BLIND_FIELD_QUALIFICATION_REVIEW_USING_EXACT_PUBLIC_ARTIFACT"
        ),
        "source_files": list(source_files),
    }
    return field_audit, ambiguity, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit frozen-scope extended Fundamental raw PIT coverage without outcomes."
    )
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--scope-manifest", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--fail-on-hard-errors", action="store_true")
    args = parser.parse_args()

    scope = pd.read_csv(args.scope_csv, dtype={"symbol": str, "market": str})
    scope_manifest = json.loads(args.scope_manifest.read_text(encoding="utf-8"))
    if scope_manifest.get("scope_version") != "capital-pit-frozen-universe-v2":
        raise SystemExit("scope manifest version mismatch")
    if int(scope_manifest.get("symbols") or 0) != len(scope):
        raise SystemExit("scope manifest symbol count mismatch")
    if int(scope_manifest.get("symbols") or 0) != 193:
        raise SystemExit("current frozen scope must contain exactly 193 symbols")
    scope.attrs["scope_version"] = scope_manifest["scope_version"]

    facts, fact_receipts = _read_many(args.shard_root, "extended_filing_facts.csv")
    coverage, coverage_receipts = _read_many(
        args.shard_root, "extended_filing_coverage.csv"
    )
    errors, error_receipts = _read_many(args.shard_root, "extended_filing_errors.csv")

    field_audit, ambiguity, summary = audit_extended_pit_coverage(
        scope=scope,
        facts=facts,
        coverage=coverage,
        errors=errors,
        source_files=fact_receipts + coverage_receipts + error_receipts,
    )
    summary["scope_manifest_sha256"] = _sha256(args.scope_manifest)
    summary["scope_csv_sha256"] = _sha256(args.scope_csv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    field_audit.to_csv(args.out_dir / "field_coverage_audit.csv", index=False)
    ambiguity.to_csv(args.out_dir / "revision_ambiguity_groups.csv", index=False)
    facts.to_csv(args.out_dir / "combined_extended_filing_facts.csv", index=False)
    coverage.to_csv(args.out_dir / "combined_extended_filing_coverage.csv", index=False)
    errors.to_csv(args.out_dir / "combined_extended_filing_errors.csv", index=False)
    (args.out_dir / "coverage_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))

    if args.fail_on_hard_errors and summary["status"] != (
        "OUTCOME_BLIND_RAW_PIT_COVERAGE_AUDIT_COMPLETE_NOT_QUALIFIED"
    ):
        raise SystemExit("extended Fundamental PIT coverage audit has hard blockers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
