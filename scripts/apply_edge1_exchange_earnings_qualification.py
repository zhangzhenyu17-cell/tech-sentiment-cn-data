from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any

import pandas as pd

from tech_sentiment.canonical_materialization import canonicalize_metadata
from tech_sentiment.exchange_earnings_materialization import (
    materialize_registered_exchange_earnings_directions,
)
from tech_sentiment.immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from tech_sentiment.major_negative_review import review_major_negative_events
from tech_sentiment.pit_public_materialization import _stable_hash, validate_materialized_pit_records
from tech_sentiment.pit_replay_audit import audit_pit_replay


CONTRACT_ID = "EDGE1_EARNINGS_EVIDENCE_QUALIFICATION_V1"
CLASSIFIER_VERSION = "issuer-explicit-guidance-v1"
SOURCES = {"SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE"}
PROGRESS_HEARTBEAT_ITEMS = 25
PROGRESS_HEARTBEAT_SECONDS = 300.0
HARD_FAILURE_CIRCUIT_BREAKER = 5


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _direction_rows(evidence: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "entity_id",
        "document_id",
        "event_date",
        "earnings_expectation_direction",
        "evidence_available_date",
        "document_url",
        "document_retrieval_url",
        "document_sha256",
        "classifier_version",
        "availability_rule",
        "source_identity",
    ]
    rows: list[dict[str, object]] = []
    for _, row in evidence.iterrows():
        payload = json.loads(str(row["evidence_payload"]))
        provenance = json.loads(str(row["provenance"]))
        rows.append(
            {
                "entity_id": str(row["entity_id"]),
                "document_id": str(row["document_id"]),
                "event_date": row["event_date"],
                "earnings_expectation_direction": str(
                    payload["earnings_expectation_direction"]
                ),
                "evidence_available_date": row["evidence_available_date"],
                "document_url": str(
                    provenance.get("document_url") or row.get("source_url_identity") or ""
                ),
                "document_retrieval_url": str(
                    provenance.get("document_retrieval_url")
                    or provenance.get("document_url")
                    or row.get("source_url_identity")
                    or ""
                ),
                "document_sha256": str(provenance["document_sha256"]),
                "classifier_version": str(payload["classifier_version"]),
                "availability_rule": str(provenance["availability_rule"]),
                "source_identity": str(row["source_identity"]),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _negative_events(earnings_evidence: pd.DataFrame) -> pd.DataFrame:
    if earnings_evidence.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, row in earnings_evidence.iterrows():
        payload = json.loads(str(row["evidence_payload"]))
        if str(payload.get("earnings_expectation_direction") or "").upper() != "DOWN":
            continue
        negative_payload = {
            "major_event_type": "EARNINGS_WARNING",
            "major_event_direction": "NEGATIVE",
            "derived_from_evidence_id": str(row["evidence_id"]),
            "deterministic_mapping": "ISSUER_EARNINGS_DIRECTION_DOWN_TO_EARNINGS_WARNING",
        }
        provenance = json.loads(str(row["provenance"]))
        provenance["negative_event_mapping"] = negative_payload["deterministic_mapping"]
        identity = {
            "source_evidence_id": str(row["evidence_id"]),
            "document_id": str(row["document_id"]),
            "revision_id": str(row["revision_id"]),
        }
        rows.append(
            {
                "evidence_id": f"earnings-warning:{_stable_hash(identity)}",
                "entity_id": str(row["entity_id"]),
                "evidence_type": "EARNINGS_WARNING",
                "event_date": row["event_date"],
                "evidence_available_date": row["evidence_available_date"],
                "source_identity": str(row["source_identity"]),
                "provider": str(row["provider"]),
                "document_id": str(row["document_id"]),
                "revision_id": f"{row['revision_id']}:NEGATIVE_MAPPING",
                "provenance": json.dumps(
                    provenance, ensure_ascii=False, sort_keys=True
                ),
                "ingestion_identity": _stable_hash(
                    {"identity": identity, "payload": negative_payload}
                ),
                "availability_state": str(row["availability_state"]),
                "evidence_payload": json.dumps(
                    negative_payload, ensure_ascii=False, sort_keys=True
                ),
                "source_url_identity": row.get("source_url_identity", ""),
            }
        )
    return (
        validate_materialized_pit_records(pd.DataFrame(rows))
        if rows
        else pd.DataFrame()
    )


def _assert_contract(contract: dict[str, Any]) -> None:
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("authorization contract identity mismatch")
    if contract.get("status") != "EXPLICIT_USER_AUTHORIZED_EVIDENCE_QUALIFICATION":
        raise ValueError("earnings qualification authorization is not active")
    frozen = contract.get("frozen_semantics")
    if not isinstance(frozen, dict):
        raise ValueError("frozen semantics missing")
    if set(frozen.get("input_source_identities") or []) != SOURCES:
        raise ValueError("authorized source identity set drifted")
    if frozen.get("classifier_version") != CLASSIFIER_VERSION:
        raise ValueError("classifier version drifted")
    required_true = (
        "official_document_body_required",
        "unknown_remains_unknown",
        "event_date_inherited",
        "evidence_available_date_inherited",
        "source_identity_inherited",
    )
    required_false = (
        "numeric_threshold_used",
        "price_or_return_used",
        "coverage_threshold_changed",
        "classifier_tokens_changed",
        "universe_changed",
        "model_changed",
        "outcome_definition_changed",
    )
    for key in required_true:
        if frozen.get(key) is not True:
            raise ValueError(f"frozen invariant missing: {key}")
    for key in required_false:
        if frozen.get(key) is not False:
            raise ValueError(f"frozen boundary drift: {key}")
    boundary = contract.get("scope_boundary")
    if not isinstance(boundary, dict):
        raise ValueError("scope boundary missing")
    if boundary.get("reuse_exact_base_artifact_symbol_scope") is not True:
        raise ValueError("base artifact scope reuse must remain exact")
    if boundary.get("scope_builder_v2_688065_correction_integrated_in_this_run") is not False:
        raise ValueError("earnings-only run cannot integrate scope-v2 correction")


def _assert_base_identity(
    *,
    root: Path,
    receipt: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    base = contract["base_qualified_artifact"]
    preupload = _read_json(root / "materialization_identity/bundle_identity_preupload.json")
    manifest = _read_json(
        root / "materialization_identity/capital_pit_materialization_manifest.json"
    )
    report = _read_json(root / "materialization_identity/qualification_report.json")
    expected = {
        "workflow_run_id": str(base["workflow_run_id"]),
        "source_commit": str(base["source_commit"]),
        "artifact_id": str(base["artifact_id"]),
        "artifact_digest": str(base["artifact_digest"]).removeprefix("sha256:"),
    }
    actual = {
        "workflow_run_id": str(receipt.get("workflow_run_id") or ""),
        "source_commit": str(receipt.get("source_commit") or ""),
        "artifact_id": str(receipt.get("artifact_id") or ""),
        "artifact_digest": str(receipt.get("artifact_digest") or "").removeprefix("sha256:"),
    }
    if actual != expected:
        raise ValueError(f"base artifact envelope mismatch: {actual}")
    if str(preupload.get("source_commit") or "") != expected["source_commit"]:
        raise ValueError("base preupload source commit mismatch")
    if str(manifest.get("source_commit") or "") != expected["source_commit"]:
        raise ValueError("base manifest source commit mismatch")
    if str(report.get("source_commit") or "") != expected["source_commit"]:
        raise ValueError("base report source commit mismatch")
    if str(report.get("overall_status") or "") != "PUBLIC_MATERIALIZATION_COMPLETED":
        raise ValueError("base public materialization status mismatch")


def _checkpoint_identity(row: pd.Series) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="edge1-exchange-earnings-direction-document",
        producer_version=f"edge1-v1+{CLASSIFIER_VERSION}",
        source_commit=CONTRACT_ID,
        source_identities=(str(row["source_identity"]),),
        query_identity={
            "entity_id": str(row["entity_id"]),
            "document_id": str(row["document_id"]),
            "revision_id": str(row["revision_id"]),
            "source_url_identity": str(row["source_url_identity"]),
        },
        scope={
            "input_evidence_type": "ISSUER_EARNINGS_FORECAST",
            "availability_state": "HISTORICAL_RECONSTRUCTABLE",
        },
    )


def _normalized_failure_signature(error: object) -> str:
    text = str(error).strip()
    text = re.sub(r"https://\\S+", "<url>", text)
    text = re.sub(r"\\b[0-9a-fA-F]{32,}\\b", "<hash>", text)
    text = re.sub(r"\\b\\d{6,}\\b", "<id>", text)
    return text[:240]


def _run_bridge_with_checkpoints(
    source: pd.DataFrame,
    *,
    checkpoint_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    store = ImmutableCheckpointStore(checkpoint_dir)
    evidence_parts: list[pd.DataFrame] = []
    unclassified_parts: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    resumed = 0
    executed = 0
    attempted_new = 0
    processed = 0
    total = int(len(source))
    started = time.monotonic()
    last_heartbeat = started
    last_failure_signature: str | None = None
    consecutive_same_hard_failures = 0
    circuit_breaker_tripped = False
    circuit_breaker_signature: str | None = None

    ordered = source.sort_values(
        ["source_identity", "entity_id", "evidence_available_date", "document_id"]
    )

    for _, row in ordered.iterrows():
        identity = _checkpoint_identity(row)
        loaded = store.load(identity)
        if loaded is not None:
            evidence = loaded.frames.get("evidence", pd.DataFrame())
            unclassified = loaded.frames.get("unclassified", pd.DataFrame())
            resumed += 1
        else:
            attempted_new += 1
            result = materialize_registered_exchange_earnings_directions(
                pd.DataFrame([row])
            )
            if len(result.errors):
                records = [
                    {k: str(v) for k, v in error.items()}
                    for error in result.errors.to_dict("records")
                ]
                errors.extend(records)
                signature = _normalized_failure_signature(
                    records[0].get("error", "UNKNOWN_HARD_FAILURE")
                )
                if signature == last_failure_signature:
                    consecutive_same_hard_failures += 1
                else:
                    last_failure_signature = signature
                    consecutive_same_hard_failures = 1

                processed += 1
                print(
                    json.dumps(
                        {
                            "event": "edge1_earnings_progress",
                            "processed": processed,
                            "total": total,
                            "resumed": resumed,
                            "attempted_new": attempted_new,
                            "executed_success": executed,
                            "direction_rows": sum(len(x) for x in evidence_parts),
                            "unclassified_rows": sum(len(x) for x in unclassified_parts),
                            "hard_errors": len(errors),
                            "last_failure_signature": signature,
                            "consecutive_same_hard_failures": consecutive_same_hard_failures,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )

                if consecutive_same_hard_failures >= HARD_FAILURE_CIRCUIT_BREAKER:
                    circuit_breaker_tripped = True
                    circuit_breaker_signature = signature
                    break
                continue

            last_failure_signature = None
            consecutive_same_hard_failures = 0
            evidence = result.evidence
            unclassified = result.unclassified
            store.save(
                identity,
                frames={
                    "evidence": evidence,
                    "unclassified": unclassified,
                },
                metadata={
                    "contract_id": CONTRACT_ID,
                    "classifier_version": CLASSIFIER_VERSION,
                    "classification_state": (
                        "EXPLICIT_DIRECTION" if len(evidence) else "UNCLASSIFIED"
                    ),
                    "formal_evidence_handoff": False,
                },
            )
            executed += 1

        if len(evidence):
            evidence_parts.append(evidence)
        if len(unclassified):
            unclassified_parts.append(unclassified)

        processed += 1
        now = time.monotonic()
        if (
            processed % PROGRESS_HEARTBEAT_ITEMS == 0
            or now - last_heartbeat >= PROGRESS_HEARTBEAT_SECONDS
            or processed == total
        ):
            elapsed = max(now - started, 1e-9)
            throughput = processed / elapsed
            remaining = max(total - processed, 0)
            eta_seconds = remaining / throughput if throughput > 0 else None
            print(
                json.dumps(
                    {
                        "event": "edge1_earnings_progress",
                        "processed": processed,
                        "total": total,
                        "resumed": resumed,
                        "attempted_new": attempted_new,
                        "executed_success": executed,
                        "direction_rows": sum(len(x) for x in evidence_parts),
                        "unclassified_rows": sum(len(x) for x in unclassified_parts),
                        "hard_errors": len(errors),
                        "elapsed_seconds": round(elapsed, 1),
                        "throughput_items_per_minute": round(throughput * 60.0, 3),
                        "eta_seconds": (
                            round(float(eta_seconds), 1)
                            if eta_seconds is not None
                            else None
                        ),
                        "checkpoint_watermark": resumed + executed,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            last_heartbeat = now

    evidence = (
        validate_materialized_pit_records(
            pd.concat(evidence_parts, ignore_index=True, sort=False)
        )
        if evidence_parts
        else pd.DataFrame()
    )
    unclassified = (
        pd.concat(unclassified_parts, ignore_index=True, sort=False)
        if unclassified_parts
        else pd.DataFrame()
    )
    error_frame = pd.DataFrame(errors)
    summary = {
        "eligible_documents": total,
        "processed_documents": int(processed),
        "resumed_documents": int(resumed),
        "attempted_new_documents": int(attempted_new),
        "executed_documents": int(executed),
        "direction_documents": int(len(evidence)),
        "unclassified_documents": int(len(unclassified)),
        "error_documents": int(len(error_frame)),
        "checkpoint_mode": "IMMUTABLE_PER_DOCUMENT_V1",
        "progress_heartbeat_items": PROGRESS_HEARTBEAT_ITEMS,
        "progress_heartbeat_seconds": PROGRESS_HEARTBEAT_SECONDS,
        "hard_failure_circuit_breaker": HARD_FAILURE_CIRCUIT_BREAKER,
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "circuit_breaker_signature": circuit_breaker_signature,
    }
    return evidence, unclassified, error_frame, summary

def _coverage_by_entity(
    source: pd.DataFrame,
    evidence: pd.DataFrame,
    unclassified: pd.DataFrame,
    errors: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    entities = sorted(set(source["entity_id"].astype(str)))
    for entity in entities:
        src = source[source["entity_id"].astype(str).eq(entity)]
        ev = (
            evidence[evidence["entity_id"].astype(str).eq(entity)]
            if len(evidence)
            else pd.DataFrame()
        )
        unc = (
            unclassified[unclassified["entity_id"].astype(str).eq(entity)]
            if len(unclassified)
            else pd.DataFrame()
        )
        err = (
            errors[errors["entity_id"].astype(str).eq(entity)]
            if len(errors) and "entity_id" in errors.columns
            else pd.DataFrame()
        )
        rows.append(
            {
                "entity_id": entity,
                "exchange_forecast_documents": int(len(src)),
                "exchange_direction_documents": int(len(ev)),
                "exchange_unclassified_documents": int(len(unc)),
                "exchange_error_documents": int(len(err)),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--base-receipt", type=Path, required=True)
    parser.add_argument("--authorization-contract", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--current-source-commit", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    args = parser.parse_args()

    root = args.artifact_root
    pit = root / "pit_evidence_materialization"
    contract = _read_json(args.authorization_contract)
    _assert_contract(contract)
    base_receipt = _read_json(args.base_receipt)
    _assert_base_identity(root=root, receipt=base_receipt, contract=contract)

    scope_path = root / "pit_symbol_scope/capital_pit_symbols.csv"
    scope_sha_before = _file_sha256(scope_path)
    scope_rows_before = len(pd.read_csv(scope_path, dtype=str))

    issuer = validate_materialized_pit_records(_read_csv(pit / "pit_evidence.csv"))
    source = issuer[
        issuer["source_identity"].astype(str).isin(SOURCES)
        & issuer["evidence_type"].astype(str).eq("ISSUER_EARNINGS_FORECAST")
        & issuer["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE")
    ].copy()
    observed = contract["base_artifact_observed_facts"]
    source_counts = source.groupby("source_identity").size().to_dict()
    entity_counts = source.groupby("source_identity")["entity_id"].nunique().to_dict()
    if int(source_counts.get("SSE_ANNOUNCEMENT_ARCHIVE", 0)) != int(
        observed["sse_issuer_earnings_forecast_documents"]
    ):
        raise ValueError("base SSE earnings forecast document count drifted")
    if int(entity_counts.get("SSE_ANNOUNCEMENT_ARCHIVE", 0)) != int(
        observed["sse_issuer_earnings_forecast_entities"]
    ):
        raise ValueError("base SSE earnings forecast entity count drifted")
    if int(source_counts.get("SZSE_ANNOUNCEMENT_ARCHIVE", 0)) != int(
        observed["szse_issuer_earnings_forecast_documents"]
    ):
        raise ValueError("base SZSE earnings forecast document count drifted")
    if int(entity_counts.get("SZSE_ANNOUNCEMENT_ARCHIVE", 0)) != int(
        observed["szse_issuer_earnings_forecast_entities"]
    ):
        raise ValueError("base SZSE earnings forecast entity count drifted")

    existing_evidence = validate_materialized_pit_records(
        _read_csv(pit / "earnings_direction_evidence.csv")
    )
    existing_sources = set(existing_evidence["source_identity"].astype(str))
    if existing_sources != {"CNINFO_ANNOUNCEMENT_ARCHIVE"}:
        raise ValueError(f"base formal direction source set drifted: {sorted(existing_sources)}")
    if len(existing_evidence) != int(observed["formal_issuer_earnings_direction_records"]):
        raise ValueError("base formal direction record count drifted")

    exchange_evidence, exchange_unclassified, errors, bridge_summary = (
        _run_bridge_with_checkpoints(
            source,
            checkpoint_dir=args.checkpoint_dir,
        )
    )

    args.diagnostics_dir.mkdir(parents=True, exist_ok=True)
    errors.to_csv(args.diagnostics_dir / "exchange_earnings_errors.csv", index=False)
    exchange_unclassified.to_csv(
        args.diagnostics_dir / "exchange_earnings_unclassified.csv", index=False
    )
    _write_json(
        args.diagnostics_dir / "bridge_progress_summary.json",
        bridge_summary,
    )
    if len(errors):
        raise RuntimeError(
            f"exchange earnings bridge has {len(errors)} document errors; fail closed"
        )

    merged_evidence = validate_materialized_pit_records(
        pd.concat(
            [existing_evidence, exchange_evidence],
            ignore_index=True,
            sort=False,
        )
    )
    if merged_evidence.duplicated(["source_identity", "document_id"]).any():
        raise ValueError("merged direction evidence has duplicate source/document identity")
    if merged_evidence["evidence_id"].astype(str).duplicated().any():
        raise ValueError("merged direction evidence has duplicate evidence_id")

    existing_directions = _read_csv(pit / "earnings_direction.csv")
    if "source_identity" not in existing_directions.columns:
        existing_directions["source_identity"] = "CNINFO_ANNOUNCEMENT_ARCHIVE"
    exchange_directions = _direction_rows(exchange_evidence)
    merged_directions = pd.concat(
        [existing_directions, exchange_directions],
        ignore_index=True,
        sort=False,
    )
    if merged_directions.duplicated(["source_identity", "document_id"]).any():
        raise ValueError("merged direction table has duplicate source/document identity")

    existing_unclassified = _read_csv(pit / "earnings_direction_unclassified.csv")
    if len(existing_unclassified) and "source_identity" not in existing_unclassified.columns:
        existing_unclassified["source_identity"] = "CNINFO_ANNOUNCEMENT_ARCHIVE"
    merged_unclassified = pd.concat(
        [existing_unclassified, exchange_unclassified],
        ignore_index=True,
        sort=False,
    )

    existing_coverage = _read_csv(pit / "earnings_direction_coverage.csv")
    exchange_coverage = _coverage_by_entity(
        source,
        exchange_evidence,
        exchange_unclassified,
        errors,
    )
    coverage = existing_coverage.copy()
    for field in (
        "forecast_documents",
        "direction_documents",
        "unclassified_documents",
    ):
        if field in coverage.columns:
            coverage[f"cninfo_{field}"] = pd.to_numeric(
                coverage[field], errors="coerce"
            ).fillna(0).astype(int)
    coverage = coverage.merge(exchange_coverage, on="entity_id", how="left")
    for field in (
        "exchange_forecast_documents",
        "exchange_direction_documents",
        "exchange_unclassified_documents",
        "exchange_error_documents",
    ):
        coverage[field] = pd.to_numeric(coverage[field], errors="coerce").fillna(0).astype(int)
    if "cninfo_forecast_documents" in coverage:
        coverage["forecast_documents"] = (
            coverage["cninfo_forecast_documents"] + coverage["exchange_forecast_documents"]
        )
    if "cninfo_direction_documents" in coverage:
        coverage["direction_documents"] = (
            coverage["cninfo_direction_documents"] + coverage["exchange_direction_documents"]
        )
    if "cninfo_unclassified_documents" in coverage:
        coverage["unclassified_documents"] = (
            coverage["cninfo_unclassified_documents"] + coverage["exchange_unclassified_documents"]
        )

    negative = _negative_events(merged_evidence)
    combined_old = validate_materialized_pit_records(
        _read_csv(pit / "pit_evidence_extended.csv")
    )
    keep = ~combined_old["evidence_type"].astype(str).eq("ISSUER_EARNINGS_DIRECTION")
    keep &= ~(
        combined_old["evidence_type"].astype(str).eq("EARNINGS_WARNING")
        & combined_old["evidence_id"].astype(str).str.startswith("earnings-warning:")
    )
    combined_parts = [combined_old.loc[keep].copy(), merged_evidence]
    if len(negative):
        combined_parts.append(negative)
    combined = validate_materialized_pit_records(
        pd.concat(combined_parts, ignore_index=True, sort=False)
    )

    coverage_ledger = _read_csv(pit / "major_negative_coverage_ledger.csv")
    review, review_summary = review_major_negative_events(
        coverage_ledger=coverage_ledger,
        evidence_records=combined,
    )
    audit = audit_pit_replay(combined)

    manifest_path = pit / "derived_pit_materialization_manifest.json"
    manifest = _read_json(manifest_path)
    earnings_summary = dict(manifest.get("earnings_materialization") or {})
    earnings_summary.update(
        {
            "source_identity": "REGISTERED_ISSUER_ANNOUNCEMENT_ARCHIVES",
            "source_identities": [
                "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "SSE_ANNOUNCEMENT_ARCHIVE",
                "SZSE_ANNOUNCEMENT_ARCHIVE",
            ],
            "forecast_documents": int(
                pd.to_numeric(coverage.get("forecast_documents", 0), errors="coerce").fillna(0).sum()
            ),
            "direction_documents": int(
                pd.to_numeric(coverage.get("direction_documents", 0), errors="coerce").fillna(0).sum()
            ),
            "canonical_evidence_records": int(len(merged_evidence)),
            "unclassified_documents": int(len(merged_unclassified)),
            "exchange_direction_documents": int(len(exchange_evidence)),
            "exchange_unclassified_documents": int(len(exchange_unclassified)),
            "exchange_error_documents": 0,
            "classifier_version": CLASSIFIER_VERSION,
            "unknown_is_not_not_down": True,
            "numeric_threshold_used": False,
            "price_or_return_used": False,
        }
    )

    overlay = {
        "contract_id": CONTRACT_ID,
        "status": "PUBLIC_EVIDENCE_MATERIALIZATION_COMPLETE_PRIVATE_VERIFICATION_REQUIRED",
        "authorization_status": contract["status"],
        "base_artifact": contract["base_qualified_artifact"],
        "scope_sha256_before": scope_sha_before,
        "scope_rows_before": int(scope_rows_before),
        "scope_sha256_after": _file_sha256(scope_path),
        "scope_rows_after": int(len(pd.read_csv(scope_path, dtype=str))),
        "scope_unchanged": _file_sha256(scope_path) == scope_sha_before,
        "classifier_version": CLASSIFIER_VERSION,
        "classifier_tokens_changed": False,
        "coverage_threshold_changed": False,
        "universe_changed": False,
        "model_changed": False,
        "outcome_definition_changed": False,
        "outcomes_read": False,
        "high_factor_materialized": False,
        "parameter_search_run": False,
        "holdout_or_oos_opened": False,
        "source_identities": sorted(SOURCES),
        "eligible_exchange_forecast_documents": int(len(source)),
        "exchange_direction_documents": int(len(exchange_evidence)),
        "exchange_unclassified_documents": int(len(exchange_unclassified)),
        "exchange_error_documents": 0,
        "formal_direction_records_before": int(len(existing_evidence)),
        "formal_direction_records_after": int(len(merged_evidence)),
        "earnings_warning_records_after": int(len(negative)),
        "major_negative_event_exclusion_complete": bool(
            review_summary.get("major_negative_event_exclusion_complete")
        ),
        "private_verification_required": True,
        "public_repo_grants_historical_qualification": False,
        "workflow_run_id": str(args.workflow_run_id),
        "source_commit": str(args.current_source_commit),
        "bridge_progress": bridge_summary,
    }
    if overlay["scope_unchanged"] is not True:
        raise ValueError("earnings-only qualification changed symbol scope")

    manifest["earnings_materialization"] = earnings_summary
    manifest["major_negative_event_exclusion_complete"] = bool(
        review_summary.get("major_negative_event_exclusion_complete")
    )
    manifest["major_negative_summary"] = review_summary
    manifest["pit_audit"] = audit
    manifest["edge1_earnings_qualification"] = overlay
    manifest["source_commit"] = str(args.current_source_commit)
    manifest = canonicalize_metadata(manifest)
    if not isinstance(manifest, dict):
        raise ValueError("canonical derived manifest is not an object")

    checkpoint_path = pit / "checkpoint_receipt_summary.json"
    checkpoint = _read_json(checkpoint_path)
    checkpoint["source_commit"] = str(args.current_source_commit)
    checkpoint["edge1_earnings_qualification"] = {
        "contract_id": CONTRACT_ID,
        "base_finalizer_source_commit": contract["base_qualified_artifact"]["source_commit"],
        "incremental_source_commit": str(args.current_source_commit),
        "stage_lineage_preserved": True,
        "stage_bundles_recomputed": False,
        "evidence_overlay_only": True,
    }

    merged_directions.to_csv(pit / "earnings_direction.csv", index=False)
    merged_evidence.to_csv(pit / "earnings_direction_evidence.csv", index=False)
    coverage.to_csv(pit / "earnings_direction_coverage.csv", index=False)
    merged_unclassified.to_csv(pit / "earnings_direction_unclassified.csv", index=False)
    review.to_csv(pit / "major_negative_review.csv", index=False)
    combined.to_csv(pit / "pit_evidence_extended.csv", index=False)
    _write_json(manifest_path, manifest)
    _write_json(checkpoint_path, checkpoint)
    _write_json(args.diagnostics_dir / "qualification_overlay.json", overlay)

    result = {
        "status": "PUBLIC_EVIDENCE_MATERIALIZATION_COMPLETE_PRIVATE_VERIFICATION_REQUIRED",
        "contract_id": CONTRACT_ID,
        "scope_unchanged": True,
        "formal_direction_records_before": int(len(existing_evidence)),
        "formal_direction_records_after": int(len(merged_evidence)),
        "exchange_direction_documents": int(len(exchange_evidence)),
        "exchange_unclassified_documents": int(len(exchange_unclassified)),
        "major_negative_event_exclusion_complete": bool(
            review_summary.get("major_negative_event_exclusion_complete")
        ),
        "pit_audit": audit,
        "outcomes_read": False,
        "private_verification_required": True,
    }
    _write_json(args.diagnostics_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
