from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping


ALLOWED_READINESS_STATES = {
    "QUALIFIED_INPUT",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
    "FORWARD_ONLY",
    "DATA_INSUFFICIENT",
    "UNAVAILABLE",
}

_BLOCKER_PRECEDENCE = (
    "UNAVAILABLE",
    "DATA_INSUFFICIENT",
    "FORWARD_ONLY",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
)


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_files(paths: Iterable[str | Path], *, root: str | Path) -> list[dict[str, object]]:
    base = Path(root).resolve()
    rows: list[dict[str, object]] = []
    for value in sorted((Path(p) for p in paths), key=lambda p: p.as_posix()):
        if not value.is_file():
            raise ValueError(f"materialized file missing: {value}")
        resolved = value.resolve()
        rows.append(
            {
                "path": resolved.relative_to(base).as_posix(),
                "sha256": file_sha256(resolved),
                "bytes": int(resolved.stat().st_size),
            }
        )
    return rows


def _state(value: object, *, fallback: str = "DATA_INSUFFICIENT") -> str:
    text = str(value or "").strip().upper()
    return text if text in ALLOWED_READINESS_STATES else fallback


def _combine_required_states(
    source_states: Mapping[str, object],
    required: tuple[str, ...],
    *,
    default: str,
) -> str:
    values = [_state(source_states.get(source, default), fallback=default) for source in required]
    if values and all(value == "QUALIFIED_INPUT" for value in values):
        return "QUALIFIED_INPUT"
    for blocker in _BLOCKER_PRECEDENCE:
        if blocker in values:
            return blocker
    return _state(default)


def build_readiness_matrix(
    *,
    capital_summary: Mapping[str, object],
    financing_summary: Mapping[str, object] | None,
    pit_summary: Mapping[str, object] | None,
) -> dict[str, str]:
    etf_state = _state(capital_summary.get("etf_readiness_state"), fallback="DATA_INSUFFICIENT")
    turnover_state = _state(
        capital_summary.get("turnover_readiness_state"), fallback="DATA_INSUFFICIENT"
    )

    financing_state = "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    if financing_summary is not None:
        qualification = str(financing_summary.get("qualification_state") or "").upper()
        coverage = float(financing_summary.get("bilateral_coverage") or 0.0)
        if qualification in {"CANONICAL_UNIT_QUALIFIED", "QUALIFIED_INPUT"} and coverage == 1.0:
            financing_state = "QUALIFIED_INPUT"
        elif coverage > 0:
            financing_state = "PARTIAL_COVERAGE"
        else:
            financing_state = "DATA_INSUFFICIENT"

    if pit_summary is None:
        source_states: dict[str, object] = {
            "CNINFO_ANNOUNCEMENT_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "SSE_ANNOUNCEMENT_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "SZSE_ANNOUNCEMENT_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "DERIVED_PIT_FUNDAMENTAL_TRENDS": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "DERIVED_PIT_TRAILING_VALUATION": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
        }
    else:
        raw_states = pit_summary.get("source_states")
        source_states = dict(raw_states) if isinstance(raw_states, Mapping) else {}

    fundamental_state = _combine_required_states(
        source_states,
        (
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
            "DERIVED_PIT_FUNDAMENTAL_TRENDS",
        ),
        default="DATA_INSUFFICIENT",
    )
    earnings_state = _combine_required_states(
        source_states,
        (
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
        ),
        default="DATA_INSUFFICIENT",
    )
    valuation_state = _combine_required_states(
        source_states,
        ("DERIVED_PIT_TRAILING_VALUATION",),
        default="HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    )
    major_event_state = _combine_required_states(
        source_states,
        (
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
        ),
        default="DATA_INSUFFICIENT",
    )
    major_negative_state = (
        "QUALIFIED_INPUT"
        if bool((pit_summary or {}).get("major_negative_event_exclusion_complete"))
        else "DATA_INSUFFICIENT"
    )

    pit_audit = (pit_summary or {}).get("pit_audit")
    audit = dict(pit_audit) if isinstance(pit_audit, Mapping) else {}
    clean_forward_required = (
        etf_state == "QUALIFIED_INPUT"
        and turnover_state == "QUALIFIED_INPUT"
        and fundamental_state == "QUALIFIED_INPUT"
        and earnings_state == "QUALIFIED_INPUT"
        and valuation_state == "QUALIFIED_INPUT"
        and major_event_state == "QUALIFIED_INPUT"
        and major_negative_state == "QUALIFIED_INPUT"
        and bool(audit.get("required_fields_complete"))
        and bool(audit.get("no_future_evidence"))
        and bool(audit.get("duplicate_identity_free"))
        and bool(audit.get("provenance_complete"))
        and bool(audit.get("prefix_replay_filter_equality"))
        and bool(audit.get("revision_identity_complete"))
    )

    matrix = {
        "588000_long_flow": etf_state,
        "sse_szse_a_shares_turnover": turnover_state,
        "financing": financing_state,
        "fundamental_pit": fundamental_state,
        "earnings_pit": earnings_state,
        "valuation_pit": valuation_state,
        "major_event_pit": major_event_state,
        "major_negative_exclusion": major_negative_state,
        "clean_forward_external_evidence": (
            "QUALIFIED_INPUT" if clean_forward_required else "DATA_INSUFFICIENT"
        ),
    }
    bad = sorted(set(matrix.values()) - ALLOWED_READINESS_STATES)
    if bad:
        raise ValueError(f"invalid readiness states: {bad}")
    return matrix


def build_materialization_manifest(
    *,
    schema_version: str,
    target_start: str,
    target_end: str,
    root: str | Path,
    files: Iterable[str | Path],
    readiness_matrix: Mapping[str, str],
    source_identities: Iterable[str],
    query_identities: Mapping[str, object] | None = None,
    workflow_run_id: str | None = None,
    source_commit: str | None = None,
    coverage_matrix: Mapping[str, object] | None = None,
    provenance_matrix: Mapping[str, object] | None = None,
) -> dict[str, object]:
    states = {str(k): str(v).upper() for k, v in readiness_matrix.items()}
    bad = sorted(set(states.values()) - ALLOWED_READINESS_STATES)
    if bad:
        raise ValueError(f"invalid readiness states: {bad}")
    sources = sorted({str(value).strip() for value in source_identities if str(value).strip()})
    if not schema_version.strip() or not sources:
        raise ValueError("schema_version and at least one source identity are required")
    manifest: dict[str, object] = {
        "schema_version": schema_version,
        "target_start": target_start,
        "target_end": target_end,
        "workflow_run_id": str(workflow_run_id or "").strip() or None,
        "source_commit": str(source_commit or "").strip() or None,
        "readiness_matrix": states,
        "coverage_matrix": dict(coverage_matrix or {}),
        "provenance_matrix": dict(provenance_matrix or {}),
        "source_identities": sources,
        "query_identities": dict(query_identities or {}),
        "files": describe_files(files, root=root),
        "production_or_model_output": False,
        "predictive_research_run": False,
        "holdout_run": False,
        "parameter_search_run": False,
        "ready_manual_only": False,
        "schedule_allowed": False,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


def write_manifest(path: str | Path, manifest: Mapping[str, object]) -> None:
    Path(path).write_text(
        json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ALLOWED_READINESS_STATES",
    "file_sha256",
    "describe_files",
    "build_readiness_matrix",
    "build_materialization_manifest",
    "write_manifest",
]
