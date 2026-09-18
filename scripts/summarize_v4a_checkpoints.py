from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from tech_sentiment.immutable_checkpoint import CHECKPOINT_SCHEMA_VERSION


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize and verify V4-A checkpoint receipts.")
    parser.add_argument("--checkpoint-root", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    root = Path(args.checkpoint_root)
    if not root.is_dir():
        raise SystemExit("checkpoint root missing")
    receipts: list[dict[str, object]] = []
    for receipt_path in sorted(root.rglob("receipt.json")):
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"checkpoint receipt is not an object: {receipt_path}")
        if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"checkpoint receipt schema mismatch: {receipt_path}")
        if payload.get("completion_state") != "COMPLETE_CHUNK":
            raise ValueError(f"checkpoint is not complete: {receipt_path}")
        identity = payload.get("identity")
        if not isinstance(identity, dict):
            raise ValueError(f"checkpoint identity missing: {receipt_path}")
        if str(identity.get("source_commit") or "") != str(args.source_commit):
            raise ValueError(f"checkpoint source commit mismatch: {receipt_path}")
        unhashed = dict(payload)
        expected_receipt_hash = unhashed.pop("receipt_sha256", None)
        actual_receipt_hash = sha256(_canonical_json(unhashed).encode("utf-8")).hexdigest()
        if expected_receipt_hash != actual_receipt_hash:
            raise ValueError(f"checkpoint receipt identity hash mismatch: {receipt_path}")
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"checkpoint receipt files missing: {receipt_path}")
        for item in files:
            if not isinstance(item, dict):
                raise ValueError(f"checkpoint receipt file entry invalid: {receipt_path}")
            file_path = receipt_path.parent / str(item.get("path") or "")
            if not file_path.is_file():
                raise ValueError(f"checkpoint payload missing: {file_path}")
            if _file_sha256(file_path) != str(item.get("sha256") or ""):
                raise ValueError(f"checkpoint payload hash mismatch: {file_path}")
            text_columns = item.get("text_columns")
            if not isinstance(text_columns, list):
                raise ValueError(f"checkpoint text-column metadata missing: {receipt_path}")
            columns = item.get("columns")
            if not isinstance(columns, list) or not set(map(str, text_columns)).issubset(
                set(map(str, columns))
            ):
                raise ValueError(f"checkpoint text-column metadata invalid: {receipt_path}")
        receipts.append(
            {
                "receipt_path": receipt_path.relative_to(root).as_posix(),
                "fingerprint": str(payload.get("fingerprint") or ""),
                "receipt_sha256": str(expected_receipt_hash or ""),
                "producer": str(identity.get("producer") or ""),
                "producer_version": str(identity.get("producer_version") or ""),
                "checkpoint_schema_version": str(payload.get("schema_version") or ""),
                "source_commit": str(identity.get("source_commit") or ""),
                "source_identities": identity.get("source_identities", []),
                "query_identity": identity.get("query_identity", {}),
                "scope": identity.get("scope", {}),
                "completion_state": "COMPLETE_CHUNK",
                "files": [
                    {
                        "name": str(item.get("name") or ""),
                        "sha256": str(item.get("sha256") or ""),
                        "rows": int(item.get("rows") or 0),
                        "columns": item.get("columns", []),
                        "text_columns": item.get("text_columns", []),
                    }
                    for item in files
                ],
            }
        )
    if not receipts:
        raise SystemExit("no complete checkpoint receipts found")
    fingerprints = [str(row["fingerprint"]) for row in receipts]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("duplicate checkpoint fingerprints")
    summary = {
        "schema_version": "v4a-checkpoint-receipt-summary-v2",
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "source_commit": str(args.source_commit),
        "receipt_count": len(receipts),
        "all_completion_states_complete": True,
        "all_source_commits_match": True,
        "receipt_identities": receipts,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"receipt_count": len(receipts), "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
