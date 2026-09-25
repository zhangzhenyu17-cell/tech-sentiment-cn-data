from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from tech_sentiment.official_filing_facts import (
    FILING_PARSER_VERSION,
    build_filing_fact_rows,
    download_official_document,
    extract_pdf_text,
)

VERSION = "fundamental-equity-parent-v11-replay-v1"
EXPECTED_PARSER_VERSION = "official-filing-facts-v11-balance-sheet-parent-equity"
EXPECTED_SOURCE_BUNDLE_ASSET = "v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17"
EXPECTED_SOURCE_BUNDLE_SHA256 = "c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e"
EXPECTED_SOURCE_BUNDLE_IDENTITY = "f558db45af7a9e8b5e69dad1275f98e93e6ee49c09914fa591edd4348cfde4c4"
EXPECTED_ENTITIES = 192
EXPECTED_PARSED_DOCUMENTS = 4667
EXPECTED_SOFT_INSUFFICIENT_DOCUMENTS = 20
EXPECTED_OLD_EQUITY_ENTITIES = 127
FACT_COLUMNS = [
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
    "filing_title",
    "document_presentation_variant",
]
REPLAY_COLUMNS = [
    "entity_id",
    "document_id",
    "document_url",
    "expected_document_sha256",
    "actual_document_sha256",
    "filing_title",
    "document_presentation_variant",
    "status",
    "equity_parent_rows",
    "error",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _csvs(bundle_root: Path, name: str) -> list[Path]:
    return sorted(bundle_root.glob(f"shards/*/{name}"))


def load_source_bundle(bundle_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    fact_paths = _csvs(bundle_root, "versioned_filing_facts.csv")
    coverage_paths = _csvs(bundle_root, "filing_coverage.csv")
    error_paths = _csvs(bundle_root, "filing_errors.csv")
    if len(fact_paths) != 16 or len(coverage_paths) != 16 or len(error_paths) != 16:
        raise ValueError("frozen V4-A Fundamental bundle must contain exactly 16 shard outputs")

    facts = pd.concat((pd.read_csv(path) for path in fact_paths), ignore_index=True)
    coverage = pd.concat((pd.read_csv(path) for path in coverage_paths), ignore_index=True)
    errors = pd.concat((pd.read_csv(path) for path in error_paths), ignore_index=True)

    if int(coverage["entity_id"].nunique()) != EXPECTED_ENTITIES:
        raise ValueError("frozen V4-A entity scope drift")
    if len(coverage) != EXPECTED_ENTITIES:
        raise ValueError("frozen V4-A coverage row count drift")
    if int(pd.to_numeric(coverage["parsed_documents"], errors="raise").sum()) != EXPECTED_PARSED_DOCUMENTS:
        raise ValueError("frozen V4-A parsed document count drift")
    if int(pd.to_numeric(coverage["soft_data_insufficient_documents"], errors="raise").sum()) != EXPECTED_SOFT_INSUFFICIENT_DOCUMENTS:
        raise ValueError("frozen V4-A soft-insufficiency count drift")
    if len(errors) != EXPECTED_SOFT_INSUFFICIENT_DOCUMENTS:
        raise ValueError("frozen V4-A error inventory drift")
    if set(errors["severity"].astype(str)) != {"SOFT_DATA_INSUFFICIENCY"}:
        raise ValueError("frozen V4-A error severity drift")
    return facts, coverage, errors


def document_inventory(facts: pd.DataFrame) -> pd.DataFrame:
    required = {
        "entity_id",
        "document_id",
        "document_url",
        "document_sha256",
        "filing_title",
        "document_presentation_variant",
        "evidence_available_date",
        "publication_timestamp",
        "source_identity",
        "provider",
        "revision_id",
    }
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"source facts missing columns: {sorted(missing)}")

    identity_cols = ["entity_id", "document_id"]
    metadata_cols = [
        "document_url",
        "document_sha256",
        "filing_title",
        "document_presentation_variant",
        "evidence_available_date",
        "publication_timestamp",
        "source_identity",
        "provider",
        "revision_id",
    ]
    rows: list[dict[str, str]] = []
    for (entity_id, document_id), group in facts.groupby(identity_cols, sort=True, dropna=False):
        row: dict[str, str] = {
            "entity_id": str(entity_id),
            "document_id": str(document_id),
        }
        for column in metadata_cols:
            values = {_clean(value) for value in group[column].tolist()}
            if len(values) != 1:
                raise ValueError(
                    f"document metadata drift: entity={entity_id} document={document_id} "
                    f"column={column} values={sorted(values)}"
                )
            row[column] = next(iter(values))
        rows.append(row)

    inventory = pd.DataFrame(rows).sort_values(identity_cols, kind="stable").reset_index(drop=True)
    if len(inventory) != EXPECTED_PARSED_DOCUMENTS:
        raise ValueError("frozen V4-A exact document inventory drift")
    if not bool(inventory["document_sha256"].str.fullmatch(r"[0-9a-f]{64}").all()):
        raise ValueError("invalid source document sha256")
    return inventory


def assigned_documents(
    inventory: pd.DataFrame,
    *,
    shard_index: int,
    shard_count: int,
    max_documents: int | None = None,
) -> pd.DataFrame:
    if shard_count <= 0 or not 0 <= shard_index < shard_count:
        raise ValueError("invalid shard identity")
    assigned = inventory.iloc[shard_index::shard_count].copy()
    if max_documents is not None:
        assigned = assigned.head(max_documents)
    return assigned.reset_index(drop=True)


def replay_document(meta: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    expected_sha = str(meta["document_sha256"])
    base = {
        "entity_id": str(meta["entity_id"]),
        "document_id": str(meta["document_id"]),
        "document_url": str(meta["document_url"]),
        "expected_document_sha256": expected_sha,
        "actual_document_sha256": "",
        "filing_title": str(meta["filing_title"]),
        "document_presentation_variant": _clean(meta["document_presentation_variant"]),
        "status": "HARD_FAILURE",
        "equity_parent_rows": 0,
        "error": "",
    }
    try:
        downloaded = download_official_document(str(meta["document_url"]))
        base["actual_document_sha256"] = downloaded.sha256
        if downloaded.sha256 != expected_sha:
            raise ValueError(
                f"exact official document sha256 mismatch expected={expected_sha} "
                f"actual={downloaded.sha256}"
            )
        text = extract_pdf_text(downloaded.content)
        parsed = build_filing_fact_rows(
            entity_id=str(meta["entity_id"]),
            title=str(meta["filing_title"]),
            evidence_available_date=str(meta["evidence_available_date"]),
            publication_timestamp=str(meta["publication_timestamp"]),
            source_identity=str(meta["source_identity"]),
            provider=str(meta["provider"]),
            document_id=str(meta["document_id"]),
            revision_id=str(meta["revision_id"]),
            document_url=str(meta["document_url"]),
            document_sha256=expected_sha,
            text=text,
        )
        equity = parsed[parsed["fact_type"].astype(str).eq("EQUITY_PARENT")].copy()
        if len(equity) > 1:
            raise ValueError("one exact document emitted multiple EQUITY_PARENT rows")
        facts: list[dict[str, object]] = []
        for row in equity.to_dict("records"):
            row["filing_title"] = str(meta["filing_title"])
            row["document_presentation_variant"] = _clean(meta["document_presentation_variant"])
            facts.append({column: row.get(column, "") for column in FACT_COLUMNS})
        base["status"] = "EQUITY_PARENT_PRESENT" if facts else "EQUITY_PARENT_MISSING"
        base["equity_parent_rows"] = len(facts)
        return facts, base
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
        return [], base


def _write_csv(path: Path, rows: Iterable[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(list(rows), columns=columns).to_csv(path, index=False)


def run_shard(args: argparse.Namespace) -> int:
    if FILING_PARSER_VERSION != EXPECTED_PARSER_VERSION:
        raise ValueError(
            f"standard parser version drift: expected={EXPECTED_PARSER_VERSION} "
            f"actual={FILING_PARSER_VERSION}"
        )
    facts, _, _ = load_source_bundle(args.bundle_root)
    inventory = document_inventory(facts)
    assigned = assigned_documents(
        inventory,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        max_documents=args.max_documents,
    )

    fact_rows: list[dict[str, object]] = []
    replay_rows: list[dict[str, object]] = []
    for meta in assigned.to_dict("records"):
        new_facts, replay = replay_document(meta)
        fact_rows.extend(new_facts)
        replay_rows.append(replay)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    facts_path = args.output_dir / "equity_parent_v11_facts.csv"
    replay_path = args.output_dir / "equity_parent_v11_replay.csv"
    _write_csv(facts_path, fact_rows, FACT_COLUMNS)
    _write_csv(replay_path, replay_rows, REPLAY_COLUMNS)

    hard = sum(row["status"] == "HARD_FAILURE" for row in replay_rows)
    summary = {
        "version": VERSION,
        "parser_version": FILING_PARSER_VERSION,
        "source_bundle_asset": EXPECTED_SOURCE_BUNDLE_ASSET,
        "source_bundle_identity": EXPECTED_SOURCE_BUNDLE_IDENTITY,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "source_documents_assigned": len(assigned),
        "source_documents_replayed": len(replay_rows),
        "equity_parent_rows": len(fact_rows),
        "equity_parent_documents": sum(
            row["status"] == "EQUITY_PARENT_PRESENT" for row in replay_rows
        ),
        "missing_equity_parent_documents": sum(
            row["status"] == "EQUITY_PARENT_MISSING" for row in replay_rows
        ),
        "hard_failure_rows": hard,
        "outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "facts_sha256": _sha256(facts_path),
        "replay_sha256": _sha256(replay_path),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if hard else 0


def _read_recursive_csv(root: Path, name: str, columns: list[str]) -> pd.DataFrame:
    paths = sorted(root.glob(f"**/{name}"))
    if not paths:
        return pd.DataFrame(columns=columns)
    frames = [pd.read_csv(path) for path in paths]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def _coverage(facts: pd.DataFrame) -> dict[str, int]:
    if facts.empty:
        return {"rows": 0, "entities": 0, "entity_period_pairs": 0}
    return {
        "rows": int(len(facts)),
        "entities": int(facts["entity_id"].astype(str).nunique()),
        "entity_period_pairs": int(
            facts[["entity_id", "period_end"]].astype(str).drop_duplicates().shape[0]
        ),
    }


def _same_timestamp_groups(facts: pd.DataFrame) -> dict[str, int]:
    if facts.empty:
        return {"multi_revision_groups": 0, "model_input_conflict_groups": 0}
    keys = [
        "entity_id",
        "period_end",
        "evidence_available_date",
        "publication_timestamp",
    ]
    multi = 0
    conflicts = 0
    for _, group in facts.groupby(keys, dropna=False, sort=False):
        if len(group) <= 1:
            continue
        multi += 1
        signatures = {
            (float(value), str(unit))
            for value, unit in group[["value", "unit"]].itertuples(index=False, name=None)
        }
        if len(signatures) > 1:
            conflicts += 1
    return {
        "multi_revision_groups": multi,
        "model_input_conflict_groups": conflicts,
    }


def run_aggregate(args: argparse.Namespace) -> int:
    if FILING_PARSER_VERSION != EXPECTED_PARSER_VERSION:
        raise ValueError("standard parser version drift")

    source_facts, source_coverage, source_errors = load_source_bundle(args.bundle_root)
    source_inventory = document_inventory(source_facts)
    replay = _read_recursive_csv(args.shard_root, "equity_parent_v11_replay.csv", REPLAY_COLUMNS)
    new_facts = _read_recursive_csv(args.shard_root, "equity_parent_v11_facts.csv", FACT_COLUMNS)

    if len(replay) != EXPECTED_PARSED_DOCUMENTS:
        raise ValueError(
            f"replay completeness mismatch expected={EXPECTED_PARSED_DOCUMENTS} actual={len(replay)}"
        )
    duplicate_replay = replay.duplicated(["entity_id", "document_id"], keep=False)
    if duplicate_replay.any():
        raise ValueError("duplicate document replay identity")
    expected_ids = set(
        source_inventory[["entity_id", "document_id"]].astype(str).itertuples(index=False, name=None)
    )
    actual_ids = set(
        replay[["entity_id", "document_id"]].astype(str).itertuples(index=False, name=None)
    )
    if actual_ids != expected_ids:
        raise ValueError("replayed exact document set differs from frozen source inventory")
    hard = replay[replay["status"].astype(str).eq("HARD_FAILURE")]
    if len(hard):
        raise ValueError(f"hard replay failures remain: {len(hard)}")

    old = source_facts[source_facts["fact_type"].astype(str).eq("EQUITY_PARENT")].copy()
    if int(old["entity_id"].astype(str).nunique()) != EXPECTED_OLD_EQUITY_ENTITIES:
        raise ValueError("old EQUITY_PARENT baseline drift")

    required_provenance = [
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
    ]
    missing_provenance = 0
    for column in required_provenance:
        if column not in new_facts:
            missing_provenance += len(new_facts)
        else:
            missing_provenance += int(
                new_facts[column].isna().sum()
                + new_facts[column].astype(str).str.strip().eq("").sum()
            )

    key = ["entity_id", "period_end", "document_id"]
    old_idx = {
        tuple(map(str, row[:3])): (float(row[3]), str(row[4]))
        for row in old[key + ["value", "unit"]].itertuples(index=False, name=None)
    }
    new_idx = {
        tuple(map(str, row[:3])): (float(row[3]), str(row[4]))
        for row in new_facts[key + ["value", "unit"]].itertuples(index=False, name=None)
    }
    added = sorted(set(new_idx) - set(old_idx))
    removed = sorted(set(old_idx) - set(new_idx))
    common = set(old_idx) & set(new_idx)
    changed = sorted(identity for identity in common if old_idx[identity] != new_idx[identity])

    audit = {
        "version": VERSION,
        "status": "OUTCOME_BLIND_EQUITY_PARENT_V11_HISTORICAL_REPLAY_COMPLETE_NOT_PRIVATE_QUALIFIED",
        "source_bundle": {
            "asset_base": EXPECTED_SOURCE_BUNDLE_ASSET,
            "archive_sha256": EXPECTED_SOURCE_BUNDLE_SHA256,
            "bundle_identity": EXPECTED_SOURCE_BUNDLE_IDENTITY,
            "entities": EXPECTED_ENTITIES,
            "parsed_documents": EXPECTED_PARSED_DOCUMENTS,
            "inherited_soft_data_insufficient_documents": EXPECTED_SOFT_INSUFFICIENT_DOCUMENTS,
            "source_soft_error_rows": int(len(source_errors)),
            "source_coverage_rows": int(len(source_coverage)),
        },
        "parser": {
            "version": FILING_PARSER_VERSION,
            "target_fact_type": "EQUITY_PARENT",
            "all_replayed_documents_sha256_bound_to_frozen_source": True,
            "ocr_used": False,
            "unit_inference_used": False,
            "substitute_document_used": False,
        },
        "old_equity_parent": _coverage(old),
        "new_equity_parent": _coverage(new_facts),
        "delta": {
            "rows": _coverage(new_facts)["rows"] - _coverage(old)["rows"],
            "entities": _coverage(new_facts)["entities"] - _coverage(old)["entities"],
            "entity_period_pairs": (
                _coverage(new_facts)["entity_period_pairs"]
                - _coverage(old)["entity_period_pairs"]
            ),
            "added_document_rows": len(added),
            "removed_document_rows": len(removed),
            "changed_value_or_unit_document_rows": len(changed),
        },
        "row_identity_diff": {
            "key": "ENTITY_X_PERIOD_END_X_DOCUMENT_ID",
            "added": [list(item) for item in added],
            "removed": [list(item) for item in removed],
            "changed_value_or_unit": [list(item) for item in changed],
        },
        "same_timestamp_model_input": _same_timestamp_groups(new_facts),
        "quality": {
            "replayed_documents": int(len(replay)),
            "hard_failure_rows": 0,
            "required_provenance_missing_cells": int(missing_provenance),
            "inherited_soft_data_insufficiency_documents": EXPECTED_SOFT_INSUFFICIENT_DOCUMENTS,
        },
        "governance": {
            "outcome_read": False,
            "historical_outcome_reread": False,
            "evidence_qualification_changed": False,
            "model_changed": False,
            "threshold_changed": False,
            "universe_changed": False,
            "production_changed": False,
            "trading_authority_changed": False,
        },
        "next_gate": "PRIVATE_OUTCOME_BLIND_EQUITY_PARENT_V11_FIELD_QUALIFICATION_AND_CORE_RAIL_RECOMPUTE",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    facts_out = args.output_dir / "equity_parent_v11_facts.csv"
    replay_out = args.output_dir / "equity_parent_v11_replay.csv"
    audit_out = args.output_dir / "equity_parent_v11_coverage_audit.json"
    new_facts.to_csv(facts_out, index=False)
    replay.to_csv(replay_out, index=False)
    audit["artifact_files"] = {
        "equity_parent_v11_facts_sha256": _sha256(facts_out),
        "equity_parent_v11_replay_sha256": _sha256(replay_out),
    }
    audit_out.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    shard = sub.add_parser("shard")
    shard.add_argument("--bundle-root", type=Path, required=True)
    shard.add_argument("--shard-index", type=int, required=True)
    shard.add_argument("--shard-count", type=int, required=True)
    shard.add_argument("--max-documents", type=int)
    shard.add_argument("--output-dir", type=Path, required=True)

    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--bundle-root", type=Path, required=True)
    aggregate.add_argument("--shard-root", type=Path, required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "shard":
        return run_shard(args)
    return run_aggregate(args)


if __name__ == "__main__":
    raise SystemExit(main())
