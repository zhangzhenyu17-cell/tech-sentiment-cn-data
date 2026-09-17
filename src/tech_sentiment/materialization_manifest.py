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


def build_readiness_matrix(
    *,
    capital_summary: Mapping[str, object],
    financing_summary: Mapping[str, object] | None,
    pit_summary: Mapping[str, object] | None,
) -> dict[str, str]:
    etf_state = str(capital_summary.get("etf_readiness_state") or "DATA_INSUFFICIENT").upper()
    turnover_state = str(
        capital_summary.get("turnover_readiness_state") or "DATA_INSUFFICIENT"
    ).upper()

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

    pit_present = pit_summary is not None
    pit_failed = int((pit_summary or {}).get("failed_symbol_queries") or 0)
    pit_records = int((pit_summary or {}).get("materialized_records") or 0)
    if not pit_present:
        issuer_state = "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    elif pit_failed > 0:
        issuer_state = "PARTIAL_COVERAGE"
    elif pit_records >= 0:
        # CNINFO alone is not enough to claim full PIT-source qualification.
        issuer_state = "PARTIAL_COVERAGE"
    else:
        issuer_state = "DATA_INSUFFICIENT"

    matrix = {
        "588000_long_flow": etf_state,
        "sse_szse_a_shares_turnover": turnover_state,
        "financing": financing_state,
        "fundamental_pit": issuer_state,
        "earnings_pit": issuer_state,
        "valuation_pit": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
        "major_event_pit": issuer_state,
        "major_negative_exclusion": "DATA_INSUFFICIENT",
        "clean_forward_external_evidence": "DATA_INSUFFICIENT",
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
        "readiness_matrix": states,
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
