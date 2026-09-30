from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_artifacts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [item for item in payload.get("artifacts", []) if isinstance(item, dict)]
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for page in payload:
            if isinstance(page, dict):
                out.extend(item for item in page.get("artifacts", []) if isinstance(item, dict))
        return out
    raise ValueError("artifact metadata payload must be object or list")


def _index(payload: Any) -> dict[str, dict[str, Any]]:
    rows = _flatten_artifacts(payload)
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or "")
        if name in indexed:
            raise ValueError(f"duplicate artifact metadata name: {name}")
        indexed[name] = row
    return indexed


def validate_metadata(*, contract_path: Path, original_json: Path, repair_json: Path) -> dict[str, Any]:
    contract = _read_json(contract_path)
    if contract.get("contract_id") != "CROSS_SECTOR_FUNDAMENTAL_EXPANSION_SHARD_B_RECOVERY_V1":
        raise ValueError("recovery contract id drift")
    original = _index(_read_json(original_json))
    repair = _index(_read_json(repair_json))
    repair_unit = int(contract["repair_unit"])
    checked = 0

    for unit_text, expected in contract["work_unit_artifacts"].items():
        unit = int(unit_text)
        source = repair if unit == repair_unit else original
        name = str(expected["name"])
        row = source.get(name)
        if row is None:
            raise ValueError(f"expected artifact missing from source run: {name}")
        if int(row.get("id", -1)) != int(expected["artifact_id"]):
            raise ValueError(f"artifact id drift: {name}")
        if str(row.get("digest") or "") != str(expected["digest"]):
            raise ValueError(f"artifact digest drift: {name}")
        if bool(row.get("expired")):
            raise ValueError(f"artifact expired: {name}")
        checked += 1

    if checked != 32:
        raise ValueError(f"artifact metadata accounting drift: {checked}")
    return {
        "contract_id": contract["contract_id"],
        "checked_artifacts": checked,
        "original_run_id": contract["original_run_id"],
        "repair_run_id": contract["repair_run_id"],
        "repair_unit": repair_unit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate pinned GitHub Actions artifact IDs/digests for Shard B recovery.")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--original-json", type=Path, required=True)
    parser.add_argument("--repair-json", type=Path, required=True)
    args = parser.parse_args()
    result = validate_metadata(
        contract_path=args.contract,
        original_json=args.original_json,
        repair_json=args.repair_json,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
