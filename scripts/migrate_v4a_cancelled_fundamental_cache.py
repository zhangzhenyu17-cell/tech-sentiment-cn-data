from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Mapping

import pandas as pd

from tech_sentiment.earnings_materialization import EARNINGS_MATERIALIZER_VERSION
from tech_sentiment.filing_materialization import FILING_PARSER_VERSION
from tech_sentiment.immutable_checkpoint import (
    CheckpointIdentity,
    ImmutableCheckpointStore,
)


ALLOWED_CURRENT_PRODUCERS = {
    ("official-filing-facts", FILING_PARSER_VERSION),
    ("issuer-earnings-direction-document", EARNINGS_MATERIALIZER_VERSION),
}


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"checkpoint receipt must be object: {path}")
    return payload


def _identity_from_receipt(receipt: Mapping[str, object]) -> CheckpointIdentity:
    raw = receipt.get("identity")
    if not isinstance(raw, Mapping):
        raise ValueError("checkpoint receipt identity missing")
    source_identities = raw.get("source_identities")
    if not isinstance(source_identities, list):
        raise ValueError("checkpoint receipt source identities invalid")
    query_identity = raw.get("query_identity")
    scope = raw.get("scope")
    if not isinstance(query_identity, Mapping) or not isinstance(scope, Mapping):
        raise ValueError("checkpoint receipt query/scope identity invalid")
    return CheckpointIdentity(
        producer=str(raw.get("producer") or ""),
        producer_version=str(raw.get("producer_version") or ""),
        source_commit=str(raw.get("source_commit") or ""),
        source_identities=tuple(map(str, source_identities)),
        query_identity=dict(query_identity),
        scope=dict(scope),
        schema_version=str(raw.get("schema_version") or ""),
    )


def _symbols_for_unit(
    symbols_csv: str | Path,
    *,
    unit_index: int,
    unit_count: int,
) -> set[str]:
    frame = pd.read_csv(symbols_csv, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("scope CSV missing symbol")
    symbols = sorted(
        {str(value).zfill(6) for value in frame["symbol"].dropna().astype(str)}
    )
    if unit_count < 1 or unit_index < 0 or unit_index >= unit_count:
        raise ValueError("invalid work-unit index/count")
    return {
        symbol
        for position, symbol in enumerate(symbols)
        if position % unit_count == unit_index
    }


def migrate(
    *,
    source_root: str | Path,
    destination_root: str | Path,
    symbols_csv: str | Path,
    unit_index: int,
    unit_count: int,
    progress_source_commit: str,
) -> dict[str, object]:
    source = Path(source_root)
    destination = Path(destination_root)
    destination.mkdir(parents=True, exist_ok=True)
    source_store = ImmutableCheckpointStore(source)
    destination_store = ImmutableCheckpointStore(destination)
    allowed_symbols = _symbols_for_unit(
        symbols_csv,
        unit_index=unit_index,
        unit_count=unit_count,
    )

    scanned = 0
    eligible = 0
    copied = 0
    already_present = 0
    producers: dict[str, int] = {}

    for receipt_path in sorted(source.glob("*/receipt.json")):
        scanned += 1
        receipt = _read_json(receipt_path)
        identity = _identity_from_receipt(receipt)
        symbol = str(identity.query_identity.get("symbol") or "").zfill(6)
        producer_key = (identity.producer, identity.producer_version)
        if identity.source_commit != str(progress_source_commit):
            continue
        if producer_key not in ALLOWED_CURRENT_PRODUCERS:
            continue
        if symbol not in allowed_symbols:
            continue

        # Fully validate the source checkpoint before any copy.
        loaded = source_store.load(identity)
        if loaded is None:
            raise ValueError(
                f"eligible bridge checkpoint failed validated load: {identity.fingerprint}"
            )
        eligible += 1

        existing = destination_store.load(identity)
        if existing is not None:
            already_present += 1
            continue

        source_dir = receipt_path.parent
        target_dir = destination / identity.fingerprint
        if target_dir.exists():
            raise ValueError(
                f"destination checkpoint directory exists without valid checkpoint: {target_dir}"
            )
        shutil.copytree(source_dir, target_dir)
        if destination_store.load(identity) is None:
            raise ValueError(
                f"migrated checkpoint failed destination validation: {identity.fingerprint}"
            )
        copied += 1
        producers[identity.producer] = producers.get(identity.producer, 0) + 1

    return {
        "schema_version": "v4a-cancelled-run-progress-bridge-v1",
        "source_root": str(source),
        "destination_root": str(destination),
        "progress_source_commit": str(progress_source_commit),
        "unit_index": int(unit_index),
        "unit_count": int(unit_count),
        "symbols": sorted(allowed_symbols),
        "scanned_checkpoint_receipts": scanned,
        "eligible_current_v9_checkpoints": eligible,
        "copied_checkpoints": copied,
        "already_present_checkpoints": already_present,
        "copied_by_producer": producers,
        "formal_evidence_handoff": False,
        "qualification_granted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate validated V9 checkpoints from a cancelled legacy-layout cache."
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--destination-root", required=True)
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--unit-index", type=int, required=True)
    parser.add_argument("--unit-count", type=int, required=True)
    parser.add_argument("--progress-source-commit", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    payload = migrate(
        source_root=args.source_root,
        destination_root=args.destination_root,
        symbols_csv=args.symbols_csv,
        unit_index=args.unit_index,
        unit_count=args.unit_count,
        progress_source_commit=args.progress_source_commit,
    )
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
