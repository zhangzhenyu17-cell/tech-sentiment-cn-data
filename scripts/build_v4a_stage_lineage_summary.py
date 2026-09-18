from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from tech_sentiment.v4a_persistent_stage import PERSISTENT_STAGE_SCHEMA


SCHEMA_VERSION = "v4a-checkpoint-receipt-summary-v3-stage-lineage"


def _canonical_bundle_ref(payload: Mapping[str, object]) -> dict[str, object]:
    required = (
        "family",
        "stage_kind",
        "stage_id",
        "original_source_commit",
        "producer_fingerprint",
        "compatibility_key",
        "bundle_identity",
        "archive_sha256",
        "stage_receipt_sha256",
        "start_date",
        "end_date",
    )
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise ValueError(f"persistent stage lineage fields missing: {missing}")
    return {
        key: payload.get(key)
        for key in required
    } | {
        "input_bundles": payload.get("input_bundles", {}),
        "completion_state": "COMPLETE_STAGE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the V4-A reusable-stage lineage summary for final v4a2 handoff."
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--bundle-manifest", action="append", default=[], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    manifests: dict[str, dict[str, object]] = {}
    for value in args.bundle_manifest:
        if "=" not in value:
            raise SystemExit("--bundle-manifest must use NAME=PATH")
        name, raw_path = value.split("=", 1)
        name = name.strip()
        path = Path(raw_path.strip())
        if not name or name in manifests:
            raise ValueError(f"invalid or duplicate lineage name: {name!r}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"bundle manifest must be an object: {path}")
        if payload.get("schema_version") != PERSISTENT_STAGE_SCHEMA:
            raise ValueError(f"bundle manifest schema mismatch: {path}")
        if str(payload.get("start_date") or "") != args.start_date:
            raise ValueError(f"bundle start date mismatch: {name}")
        if str(payload.get("end_date") or "") != args.end_date:
            raise ValueError(f"bundle end date mismatch: {name}")
        manifests[name] = payload

    required_names = {
        "shared",
        "capital",
        "financing",
        "issuer_cninfo",
        "issuer_sse",
        "issuer_szse",
        "issuer_aggregate",
        "fundamental",
        "prices",
        "policy",
        "derived",
    }
    if set(manifests) != required_names:
        raise ValueError(
            "stage lineage manifest set mismatch: "
            f"missing={sorted(required_names - set(manifests))} "
            f"extra={sorted(set(manifests) - required_names)}"
        )

    def identity(name: str) -> str:
        return str(manifests[name].get("bundle_identity") or "")

    expected_edges = {
        "capital": {"shared": identity("shared")},
        "financing": {"shared": identity("shared")},
        "issuer_cninfo": {"shared": identity("shared")},
        "issuer_sse": {"shared": identity("shared")},
        "issuer_szse": {"shared": identity("shared")},
        "issuer_aggregate": {
            "shared": identity("shared"),
            "cninfo": identity("issuer_cninfo"),
            "sse": identity("issuer_sse"),
            "szse": identity("issuer_szse"),
        },
        "fundamental": {"shared": identity("shared")},
        "prices": {"shared": identity("shared")},
        "policy": {"shared": identity("shared")},
        "derived": {
            "shared": identity("shared"),
            "issuer": identity("issuer_aggregate"),
            "fundamental": identity("fundamental"),
            "prices": identity("prices"),
            "policy": identity("policy"),
        },
    }
    for name, expected in expected_edges.items():
        actual_raw = manifests[name].get("input_bundles")
        if not isinstance(actual_raw, Mapping):
            raise ValueError(f"stage input lineage missing: {name}")
        actual = {
            str(key): str(value.get("bundle_identity") or "")
            for key, value in actual_raw.items()
            if isinstance(value, Mapping)
        }
        if actual != expected:
            raise ValueError(
                f"stage input lineage mismatch for {name}: "
                f"expected={expected} actual={actual}"
            )

    lineage = {
        name: _canonical_bundle_ref(manifests[name])
        for name in sorted(manifests)
    }
    original_commits = {
        str(row["original_source_commit"]) for row in lineage.values()
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": str(args.source_commit),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "receipt_count": len(lineage),
        "all_completion_states_complete": True,
        "all_source_commits_match": (
            len(original_commits) == 1
            and original_commits == {str(args.source_commit)}
        ),
        "stage_compatibility_validated": True,
        "lineage_mode": "PERSISTENT_STAGE_PRODUCER_FINGERPRINT_V1",
        "persistent_stage_schema": PERSISTENT_STAGE_SCHEMA,
        "stage_lineage": lineage,
        "original_source_commits": sorted(original_commits),
        "private_qualification_semantics_unchanged": True,
        "evidence_eligibility_changed": False,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
