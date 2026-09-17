from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.materialization_manifest import file_sha256


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record GitHub artifact identity after the immutable V4-A bundle is uploaded."
    )
    parser.add_argument("--preupload-identity", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    preupload_path = Path(args.preupload_identity)
    payload = json.loads(preupload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("preupload identity must be a JSON object")
    expected_run = str(payload.get("workflow_run_id") or "")
    expected_commit = str(payload.get("source_commit") or "")
    if expected_run and expected_run != str(args.workflow_run_id):
        raise SystemExit("workflow run ID does not match preupload identity")
    if expected_commit and expected_commit != str(args.source_commit):
        raise SystemExit("source commit does not match preupload identity")

    receipt = {
        **payload,
        "workflow_run_id": str(args.workflow_run_id),
        "source_commit": str(args.source_commit),
        "artifact_id": str(args.artifact_id),
        "artifact_digest": str(args.artifact_digest),
        "artifact_url": str(args.artifact_url),
        "preupload_identity_sha256": file_sha256(preupload_path),
        "receipt_semantics": (
            "POST_UPLOAD_IDENTITY_ONLY; artifact identity is recorded outside the already-uploaded "
            "bundle to avoid circular hashing."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
