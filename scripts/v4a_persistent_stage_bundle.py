from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.v4a_persistent_stage import (
    compatibility_descriptor,
    package_stage_bundle,
    progress_descriptor,
    verify_and_extract_stage_bundle,
)


def _inputs(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit("--input-manifest must use NAME=PATH")
        name, path = value.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise SystemExit("--input-manifest must use NAME=PATH")
        if name in out:
            raise SystemExit(f"duplicate input manifest name: {name}")
        out[name] = path
    return out


def _emit(payload: dict[str, object], path: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build, verify, and identify reusable V4-A persistent stage bundles."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    key = sub.add_parser("key")
    key.add_argument("--repo-root", default=".")
    key.add_argument("--family", required=True)
    key.add_argument("--start-date", required=True)
    key.add_argument("--end-date", required=True)
    key.add_argument("--input-manifest", action="append", default=[])
    key.add_argument("--out", default="")

    progress_key = sub.add_parser("progress-key")
    progress_key.add_argument("--repo-root", default=".")
    progress_key.add_argument("--family", required=True)
    progress_key.add_argument("--start-date", required=True)
    progress_key.add_argument("--end-date", required=True)
    progress_key.add_argument("--input-manifest", action="append", default=[])
    progress_key.add_argument("--out", default="")

    package = sub.add_parser("package")
    package.add_argument("--repo-root", default=".")
    package.add_argument("--stage-root", required=True)
    package.add_argument("--family", required=True)
    package.add_argument("--source-commit", required=True)
    package.add_argument("--start-date", required=True)
    package.add_argument("--end-date", required=True)
    package.add_argument("--input-manifest", action="append", default=[])
    package.add_argument("--out-dir", required=True)
    package.add_argument("--out", default="")

    verify = sub.add_parser("verify")
    verify.add_argument("--repo-root", default=".")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--family", required=True)
    verify.add_argument("--start-date", required=True)
    verify.add_argument("--end-date", required=True)
    verify.add_argument("--extract-to", required=True)
    verify.add_argument("--input-manifest", action="append", default=[])
    verify.add_argument("--out", default="")

    args = parser.parse_args()
    inputs = _inputs(args.input_manifest)
    if args.command == "key":
        payload = compatibility_descriptor(
            repo_root=args.repo_root,
            family=args.family,
            start_date=args.start_date,
            end_date=args.end_date,
            input_manifests=inputs,
        )
    elif args.command == "progress-key":
        payload = progress_descriptor(
            repo_root=args.repo_root,
            family=args.family,
            start_date=args.start_date,
            end_date=args.end_date,
            input_manifests=inputs,
        )
    elif args.command == "package":
        payload = package_stage_bundle(
            repo_root=args.repo_root,
            stage_root=args.stage_root,
            family=args.family,
            source_commit=args.source_commit,
            start_date=args.start_date,
            end_date=args.end_date,
            out_dir=args.out_dir,
            input_manifests=inputs,
        )
    else:
        payload = verify_and_extract_stage_bundle(
            repo_root=args.repo_root,
            archive_path=args.archive,
            manifest_path=args.manifest,
            family=args.family,
            start_date=args.start_date,
            end_date=args.end_date,
            extract_to=args.extract_to,
            input_manifests=inputs,
        )
    _emit(payload, args.out or None)


if __name__ == "__main__":
    main()
