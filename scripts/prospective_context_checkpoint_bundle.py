from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.prospective_context_checkpoint_v1 import (
    CHECKPOINT_RELEASE_TAG,
    expected_checkpoint_assets,
    package_complete_checkpoints,
    plan_available_checkpoint_assets,
    restore_checkpoint_bundle,
)
from tech_sentiment.prospective_context_raw_v1 import capture_trading_dates


def _write(payload: dict[str, object], out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _contract(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("contract_id") != "prospective_context_raw_v1":
        raise SystemExit("unexpected prospective raw contract")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve, package and restore durable prospective raw checkpoints."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    expected = sub.add_parser("expected")
    expected.add_argument("--repo-root", default=".")
    expected.add_argument(
        "--contract",
        default="reference/prospective_context_raw_v1.json",
    )
    expected.add_argument("--operation-date", required=True)
    expected.add_argument("--out", default="")

    plan = sub.add_parser("plan")
    plan.add_argument("--expected", required=True)
    plan.add_argument("--remote-assets", required=True)
    plan.add_argument("--out", default="")

    package = sub.add_parser("package")
    package.add_argument(
        "--cache-root",
        default=".cache/prospective_context_raw",
    )
    package.add_argument(
        "--out-dir",
        default="dist/prospective_context_checkpoints",
    )
    package.add_argument("--out", default="")

    restore = sub.add_parser("restore")
    restore.add_argument("--asset-dir", required=True)
    restore.add_argument(
        "--cache-root",
        default=".cache/prospective_context_raw",
    )
    restore.add_argument("--out", default="")

    args = parser.parse_args()

    if args.command == "expected":
        contract = _contract(args.contract)
        warmup = int(contract["warmup"]["trading_days"])
        dates = capture_trading_dates(
            args.operation_date,
            warmup_trading_days=warmup,
        )
        assets = expected_checkpoint_assets(
            args.repo_root,
            contract=contract,
            operation_date=args.operation_date,
            trading_dates=dates,
        )
        payload = {
            "schema_version": "prospective-context-checkpoint-expected-v1",
            "release_tag": CHECKPOINT_RELEASE_TAG,
            "operation_date": args.operation_date,
            "trading_days": int(len(dates)),
            "start_date": str(dates.min().date()),
            "end_date": str(dates.max().date()),
            "assets": assets,
        }
    elif args.command == "plan":
        expected_payload = json.loads(
            Path(args.expected).read_text(encoding="utf-8")
        )
        remote_names = Path(args.remote_assets).read_text(
            encoding="utf-8"
        ).splitlines()
        payload = plan_available_checkpoint_assets(
            expected_payload.get("assets") or [],
            remote_names,
        )
    elif args.command == "package":
        payload = package_complete_checkpoints(
            args.cache_root,
            out_dir=args.out_dir,
        )
    else:
        asset_dir = Path(args.asset_dir)
        restored = []
        for manifest in sorted(asset_dir.glob("*.manifest.json")):
            base = manifest.name[: -len(".manifest.json")]
            archive = asset_dir / f"{base}.tar.gz"
            if not archive.is_file():
                raise SystemExit(
                    f"checkpoint archive missing for manifest: {base}"
                )
            restored.append(
                restore_checkpoint_bundle(
                    archive_path=archive,
                    manifest_path=manifest,
                    cache_root=args.cache_root,
                )
            )
        payload = {
            "schema_version": "prospective-context-checkpoint-restore-v1",
            "release_tag": CHECKPOINT_RELEASE_TAG,
            "restored_count": len(restored),
            "restored": restored,
        }

    _write(payload, args.out or None)


if __name__ == "__main__":
    main()
