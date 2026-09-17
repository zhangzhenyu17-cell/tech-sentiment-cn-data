from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable
import json


READINESS_STATES = {
    "QUALIFIED_INPUT",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
    "FORWARD_ONLY",
    "DATA_INSUFFICIENT",
    "UNAVAILABLE",
}


@dataclass(frozen=True)
class MaterializedAsset:
    name: str
    path: str
    sha256: str
    rows: int
    state: str
    source_identity: str
    provider: str
    provenance: str
    immutable_version: str
    revision_semantics: str

    def __post_init__(self) -> None:
        if self.state not in READINESS_STATES:
            raise ValueError(f"invalid readiness state: {self.state}")
        if not self.name.strip() or not self.path.strip():
            raise ValueError("materialized asset requires name/path")
        if len(self.sha256) != 64:
            raise ValueError("materialized asset requires sha256")
        for field in (
            self.source_identity,
            self.provider,
            self.provenance,
            self.immutable_version,
            self.revision_semantics,
        ):
            if not str(field).strip():
                raise ValueError("materialized asset metadata cannot be empty")
        if self.rows < 0:
            raise ValueError("rows cannot be negative")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    assets: Iterable[MaterializedAsset],
    *,
    output_path: Path,
    repository_sha: str,
    generated_at: str,
    production_or_model_output: bool = False,
) -> dict[str, object]:
    rows = [asdict(asset) for asset in assets]
    if not repository_sha.strip() or not generated_at.strip():
        raise ValueError("repository_sha/generated_at required")
    manifest = {
        "schema_version": "capital-pit-materialization-v1",
        "repository_sha": repository_sha,
        "generated_at": generated_at,
        "production_or_model_output": bool(production_or_model_output),
        "assets": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "READINESS_STATES",
    "MaterializedAsset",
    "file_sha256",
    "write_manifest",
]
