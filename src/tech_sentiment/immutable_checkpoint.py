from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

import pandas as pd


CHECKPOINT_SCHEMA_VERSION = "v4a-checkpoint-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CheckpointIdentity:
    producer: str
    producer_version: str
    source_commit: str
    source_identities: tuple[str, ...]
    query_identity: Mapping[str, object]
    scope: Mapping[str, object]
    schema_version: str = CHECKPOINT_SCHEMA_VERSION

    def canonical(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source_identities"] = sorted(
            {str(value) for value in self.source_identities}
        )
        return payload

    @property
    def fingerprint(self) -> str:
        return _hash_bytes(_canonical_json(self.canonical()).encode("utf-8"))


@dataclass(frozen=True)
class CheckpointLoadResult:
    frames: dict[str, pd.DataFrame]
    receipt: dict[str, object]


class ImmutableCheckpointStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, identity: CheckpointIdentity) -> Path:
        return self.root / identity.fingerprint

    def save(
        self,
        identity: CheckpointIdentity,
        *,
        frames: Mapping[str, pd.DataFrame],
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if not frames:
            raise ValueError("checkpoint must contain at least one frame")
        target = self._dir(identity)
        target.mkdir(parents=True, exist_ok=True)
        files: list[dict[str, object]] = []
        for name, frame in sorted(frames.items()):
            if not name or "/" in name or "\\" in name:
                raise ValueError(f"invalid checkpoint frame name: {name}")
            path = target / f"{name}.csv"
            content = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=target
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            files.append(
                {
                    "name": name,
                    "path": path.name,
                    "sha256": _hash_bytes(content),
                    "bytes": len(content),
                    "rows": int(len(frame)),
                    "columns": list(map(str, frame.columns)),
                }
            )
        receipt: dict[str, object] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "fingerprint": identity.fingerprint,
            "identity": identity.canonical(),
            "files": files,
            "metadata": dict(metadata or {}),
            "completion_state": "COMPLETE_CHUNK",
        }
        receipt_without_hash = dict(receipt)
        receipt["receipt_sha256"] = _hash_bytes(
            _canonical_json(receipt_without_hash).encode("utf-8")
        )
        receipt_path = target / "receipt.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return receipt

    def load(self, identity: CheckpointIdentity) -> CheckpointLoadResult | None:
        target = self._dir(identity)
        receipt_path = target / "receipt.json"
        if not receipt_path.is_file():
            return None
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("checkpoint receipt schema mismatch")
        if receipt.get("fingerprint") != identity.fingerprint:
            raise ValueError("checkpoint fingerprint mismatch")
        if receipt.get("identity") != identity.canonical():
            raise ValueError("checkpoint identity mismatch")
        if receipt.get("completion_state") != "COMPLETE_CHUNK":
            raise ValueError("checkpoint is not a complete chunk")
        expected_receipt_hash = receipt.get("receipt_sha256")
        unhashed = dict(receipt)
        unhashed.pop("receipt_sha256", None)
        if expected_receipt_hash != _hash_bytes(
            _canonical_json(unhashed).encode("utf-8")
        ):
            raise ValueError("checkpoint receipt hash mismatch")

        frames: dict[str, pd.DataFrame] = {}
        files = receipt.get("files")
        if not isinstance(files, Sequence) or not files:
            raise ValueError("checkpoint receipt has no files")
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("checkpoint file receipt must be an object")
            name = str(item.get("name") or "")
            path = target / str(item.get("path") or "")
            if not path.is_file():
                raise ValueError(f"checkpoint file missing: {path.name}")
            if _hash_file(path) != item.get("sha256"):
                raise ValueError(f"checkpoint file hash mismatch: {path.name}")
            expected_columns = list(item.get("columns") or [])
            try:
                frame = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                if int(item.get("rows") or 0) != 0 or expected_columns:
                    raise ValueError(f"checkpoint empty CSV schema mismatch: {path.name}")
                frame = pd.DataFrame(columns=expected_columns)
            if list(map(str, frame.columns)) != expected_columns:
                raise ValueError(f"checkpoint columns mismatch: {path.name}")
            if int(len(frame)) != int(item.get("rows") or 0):
                raise ValueError(f"checkpoint row count mismatch: {path.name}")
            frames[name] = frame
        return CheckpointLoadResult(frames=frames, receipt=receipt)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointIdentity",
    "CheckpointLoadResult",
    "ImmutableCheckpointStore",
]
