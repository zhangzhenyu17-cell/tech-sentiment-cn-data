from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable


ALLOWED_READINESS_STATES = {
    "QUALIFIED_INPUT",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
    "FORWARD_ONLY",
    "DATA_INSUFFICIENT",
    "UNAVAILABLE",
}


@dataclass(frozen=True)
class MaterializedFile:
    path: str
    sha256: str
    bytes: int


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_files(paths: Iterable[str | Path], *, root: str | Path | None = None) -> list[dict[str, object]]:
    base = Path(root).resolve() if root is not None else None
    rows: list[dict[str, object]] = []
    for value in sorted((Path(p) for p in paths), key=lambda p: p.as_posix()):
        if not value.is_file():
            raise ValueError(f"materialized file missing: {value}")
        resolved = value.resolve()
        display = resolved.relative_to(base).as_posix() if base is not None else value.as_posix()
        rows.append(
            {
                "path": display,
                "sha256": file_sha256(resolved),
                "bytes": int(resolved.stat().st_size),
            }
        )
    return rows


def build_materialization_manifest(
    *,
    dataset_id: str,
    schema_version: str,
    readiness_state: str,
    target_start: str,
    target_end: str,
    source_identities: Iterable[str],
    provider_interfaces: Iterable[str],
    files: Iterable[str | Path],
    root: str | Path | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    state = str(readiness_state).strip().upper()
    if state not in ALLOWED_READINESS_STATES:
        raise ValueError(f"invalid readiness_state: {readiness_state}")
    sources = tuple(sorted({str(x).strip() for x in source_identities if str(x).strip()}))
    providers = tuple(sorted({str(x).strip() for x in provider_interfaces if str(x).strip()}))
    if not dataset_id.strip() or not schema_version.strip():
        raise ValueError("dataset_id and schema_version are required")
    if state == "QUALIFIED_INPUT" and (not sources or not providers):
        raise ValueError("QUALIFIED_INPUT requires source identity and provider interface")
    manifest: dict[str, object] = {
        "dataset_id": dataset_id,
        "schema_version": schema_version,
        "readiness_state": state,
        "target_start": target_start,
        "target_end": target_end,
        "source_identities": list(sources),
        "provider_interfaces": list(providers),
        "files": describe_files(files, root=root),
        "metadata": metadata or {},
    }
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    manifest["manifest_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


def write_manifest(path: str | Path, manifest: dict[str, object]) -> None:
    Path(path).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ALLOWED_READINESS_STATES",
    "MaterializedFile",
    "file_sha256",
    "describe_files",
    "build_materialization_manifest",
    "write_manifest",
]
