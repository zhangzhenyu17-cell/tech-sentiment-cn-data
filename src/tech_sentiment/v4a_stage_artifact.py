from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping


STAGE_RECEIPT_SCHEMA = "v4a-stage-artifact-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe stage path: {value!r}")
    return path.as_posix()


def build_stage_receipt(
    *,
    root: str | Path,
    files: Iterable[str | Path],
    stage_kind: str,
    stage_id: str,
    source_commit: str,
    start_date: str,
    end_date: str,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    base = Path(root).resolve()
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in sorted((Path(p) for p in files), key=lambda p: p.as_posix()):
        resolved = value.resolve()
        if not resolved.is_file():
            raise ValueError(f"stage file missing: {value}")
        relative = _safe_relative(resolved.relative_to(base).as_posix())
        if relative in seen:
            raise ValueError(f"duplicate stage path: {relative}")
        seen.add(relative)
        rows.append(
            {
                "path": relative,
                "sha256": file_sha256(resolved),
                "bytes": int(resolved.stat().st_size),
            }
        )
    if not rows:
        raise ValueError("stage receipt requires at least one file")
    payload: dict[str, object] = {
        "schema_version": STAGE_RECEIPT_SCHEMA,
        "completion_state": "COMPLETE_STAGE",
        "stage_kind": str(stage_kind),
        "stage_id": str(stage_id),
        "source_commit": str(source_commit),
        "start_date": str(start_date),
        "end_date": str(end_date),
        "metadata": dict(metadata or {}),
        "files": rows,
    }
    payload["receipt_sha256"] = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return payload


def write_stage_receipt(path: str | Path, payload: Mapping[str, object]) -> None:
    Path(path).write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_stage_receipt(
    *,
    root: str | Path,
    receipt_path: str | Path,
    source_commit: str,
    stage_kind: str | None = None,
    stage_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, object]:
    base = Path(root).resolve()
    path = Path(receipt_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stage receipt must be a JSON object")
    if payload.get("schema_version") != STAGE_RECEIPT_SCHEMA:
        raise ValueError("stage receipt schema mismatch")
    if payload.get("completion_state") != "COMPLETE_STAGE":
        raise ValueError("stage receipt is not complete")
    if str(payload.get("source_commit") or "") != str(source_commit):
        raise ValueError("stage receipt source commit mismatch")
    if stage_kind is not None and str(payload.get("stage_kind") or "") != str(stage_kind):
        raise ValueError("stage receipt kind mismatch")
    if stage_id is not None and str(payload.get("stage_id") or "") != str(stage_id):
        raise ValueError("stage receipt id mismatch")
    if start_date is not None and str(payload.get("start_date") or "") != str(start_date):
        raise ValueError("stage receipt start date mismatch")
    if end_date is not None and str(payload.get("end_date") or "") != str(end_date):
        raise ValueError("stage receipt end date mismatch")

    unhashed = dict(payload)
    expected = str(unhashed.pop("receipt_sha256", "") or "")
    actual = sha256(_canonical_json(unhashed).encode("utf-8")).hexdigest()
    if expected != actual:
        raise ValueError("stage receipt identity hash mismatch")

    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("stage receipt files missing")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("stage receipt file row invalid")
        relative = _safe_relative(str(item.get("path") or ""))
        if relative in seen:
            raise ValueError(f"duplicate stage receipt path: {relative}")
        seen.add(relative)
        file_path = base / relative
        if not file_path.is_file():
            raise ValueError(f"stage payload missing: {relative}")
        if file_sha256(file_path) != str(item.get("sha256") or ""):
            raise ValueError(f"stage payload hash mismatch: {relative}")
        if int(item.get("bytes") or -1) != file_path.stat().st_size:
            raise ValueError(f"stage payload size mismatch: {relative}")
    return payload


__all__ = [
    "STAGE_RECEIPT_SCHEMA",
    "file_sha256",
    "build_stage_receipt",
    "write_stage_receipt",
    "verify_stage_receipt",
]
