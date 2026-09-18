from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


QUALIFIED = "QUALIFIED_INPUT"
ISSUER_SOURCES = (
    "CNINFO_ANNOUNCEMENT_ARCHIVE",
    "SSE_ANNOUNCEMENT_ARCHIVE",
    "SZSE_ANNOUNCEMENT_ARCHIVE",
)
DERIVED_SOURCES = (
    *ISSUER_SOURCES,
    "DERIVED_PIT_FUNDAMENTAL_TRENDS",
    "DERIVED_PIT_TRAILING_VALUATION",
    "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
)
AUDIT_REQUIRED = (
    "required_fields_complete",
    "no_future_evidence",
    "duplicate_identity_free",
    "provenance_complete",
    "prefix_replay_filter_equality",
    "as_of_replay_equality",
    "revision_identity_complete",
    "later_revision_does_not_rewrite_prior_rows",
)


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"required stage file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"stage JSON must be an object: {path}")
    return payload


def _coverage_blockers(path: Path, *, label: str) -> list[str]:
    if not path.is_file():
        return [f"{label}:missing_coverage"]
    frame = pd.read_csv(path)
    if frame.empty:
        return [f"{label}:empty_coverage"]
    if "query_status" not in frame.columns:
        return [f"{label}:missing_query_status"]
    statuses = frame["query_status"].astype(str)
    bad = frame.loc[~statuses.eq("COMPLETE_WINDOW")]
    if bad.empty:
        return []
    details = sorted(set(bad["query_status"].astype(str)))
    return [f"{label}:non_complete_status={','.join(details)}"]


def stage_blockers(kind: str, root: Path) -> list[str]:
    blockers: list[str] = []

    if kind == "capital":
        summary = _read_json(root / "qualification_summary.json")
        for field in ("etf_readiness_state", "turnover_readiness_state"):
            state = str(summary.get(field) or "DATA_INSUFFICIENT")
            if state != QUALIFIED:
                blockers.append(f"{field}={state}")

    elif kind == "financing":
        summary = _read_json(root / "financing_manifest.json")
        state = str(summary.get("qualification_state") or "DATA_INSUFFICIENT")
        try:
            coverage = float(summary.get("bilateral_coverage") or 0.0)
        except (TypeError, ValueError):
            coverage = 0.0
        if state not in {"CANONICAL_UNIT_QUALIFIED", QUALIFIED}:
            blockers.append(f"qualification_state={state}")
        if coverage != 1.0:
            blockers.append(f"bilateral_coverage={coverage}")

    elif kind == "issuer":
        manifest = _read_json(root / "pit_materialization_manifest.json")
        raw_states = manifest.get("source_states")
        states = dict(raw_states) if isinstance(raw_states, dict) else {}
        selected = manifest.get("selected_sources")
        selected_sources = (
            [str(value) for value in selected]
            if isinstance(selected, list)
            else sorted(states)
        )
        if not selected_sources:
            blockers.append("issuer:no_selected_sources")
        for source in selected_sources:
            state = str(states.get(source) or "DATA_INSUFFICIENT")
            if state != QUALIFIED:
                blockers.append(f"{source}={state}")
        try:
            failed = int(manifest.get("failed_symbol_queries") or 0)
        except (TypeError, ValueError):
            failed = 1
        if failed:
            blockers.append(f"failed_symbol_queries={failed}")

    elif kind == "fundamental_earnings":
        blockers.extend(
            _coverage_blockers(root / "filing_coverage.csv", label="filings")
        )
        blockers.extend(
            _coverage_blockers(
                root / "earnings_direction_coverage.csv",
                label="earnings",
            )
        )

    elif kind == "policy":
        manifest = _read_json(root / "stage_manifest.json")
        raw = manifest.get("policy_materialization")
        summary = dict(raw) if isinstance(raw, dict) else {}
        state = str(summary.get("readiness_state") or "DATA_INSUFFICIENT")
        if state != QUALIFIED:
            blockers.append(f"policy_readiness_state={state}")
        if summary.get("source_coverage_complete") is not True:
            blockers.append("policy_source_coverage_complete=false")

    elif kind == "derived":
        summary = _read_json(root / "derived_pit_materialization_manifest.json")
        raw_states = summary.get("source_states")
        states = dict(raw_states) if isinstance(raw_states, dict) else {}
        for source in DERIVED_SOURCES:
            state = str(states.get(source) or "DATA_INSUFFICIENT")
            if state != QUALIFIED:
                blockers.append(f"{source}={state}")
        earnings = str(
            summary.get("earnings_direction_readiness_state")
            or "DATA_INSUFFICIENT"
        )
        if earnings != QUALIFIED:
            blockers.append(f"earnings_direction_readiness_state={earnings}")
        if summary.get("major_negative_event_exclusion_complete") is not True:
            blockers.append("major_negative_event_exclusion_complete=false")
        raw_audit = summary.get("pit_audit")
        audit = dict(raw_audit) if isinstance(raw_audit, dict) else {}
        for field in AUDIT_REQUIRED:
            if audit.get(field) is not True:
                blockers.append(f"pit_audit.{field}=false")

    else:
        raise ValueError(f"unsupported stage kind: {kind}")

    return blockers


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fail early only when a completed formal V4-A stage is already "
            "guaranteed to block final qualification under existing rules."
        )
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=(
            "capital",
            "financing",
            "issuer",
            "fundamental_earnings",
            "policy",
            "derived",
        ),
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    blockers = stage_blockers(args.kind, Path(args.root))
    payload = {
        "status": "V4A_STAGE_QUALIFIABLE" if not blockers else "V4A_STAGE_BLOCKED",
        "kind": args.kind,
        "root": str(Path(args.root)),
        "blockers": blockers,
        "research_run": False,
        "evidence_eligibility_changed": False,
        "production_authority_changed": False,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    if blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
