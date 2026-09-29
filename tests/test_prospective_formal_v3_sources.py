from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.prospective_formal_v3_sources import (
    load_formal_v3_capital_sources,
)


def _package(
    root: Path,
    *,
    source_key: str,
    first_observed: str,
    row: dict[str, object],
) -> None:
    session = "2026-09-29"
    decision = "2026-09-30"
    tag = f"prospective-source-observation-v3-{session}-{source_key}"
    target = root / source_key
    content = target / "content"
    content.mkdir(parents=True)
    receipt = {
        "schema_version": "prospective-source-observation-v3",
        "source_key": source_key,
        "state": "COMPLETE",
        "market_session_date": session,
        "decision_date": decision,
        "first_observed_at_asia_shanghai": first_observed,
        "observation_timestamp_semantics": "SOURCE_FETCH_COMPLETION_TIME",
        "shadow_decision_eligible": True,
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "forward_outcomes_read": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "transport_origin": "GITHUB_HOSTED",
        "producer_git_sha": "abc",
    }
    receipt_path = content / "SOURCE_OBSERVATION_RECEIPT.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame([row]).to_csv(content / "data.csv", index=False)
    (content / "errors.csv").write_text("error\n", encoding="utf-8")

    archive = target / f"{tag}.tar.gz"
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as bundle:
                for name in (
                    "SOURCE_OBSERVATION_RECEIPT.json",
                    "data.csv",
                    "errors.csv",
                ):
                    path = content / name
                    info = bundle.gettarinfo(
                        str(path), arcname=f"source_observation/{name}"
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        bundle.addfile(info, handle)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / f"{tag}.sha256").write_text(
        f"{archive_sha}  {archive.name}\n", encoding="utf-8"
    )
    identity = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "prospective-source-observation-package-v3",
        "release_tag": tag,
        "source_key": source_key,
        "market_session_date": session,
        "decision_date": decision,
        "source_observation_identity": identity,
        "archive": archive.name,
        "archive_sha256": archive_sha,
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "first_observed_at_asia_shanghai": first_observed,
        "observation_timestamp_semantics": "SOURCE_FETCH_COMPLETION_TIME",
        "shadow_decision_eligible": True,
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
    }
    (target / f"{tag}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _all_packages(root: Path, *, first_observed: str = "2026-09-30T08:30:00+08:00") -> None:
    _package(
        root,
        source_key="SSE_588000",
        first_observed=first_observed,
        row={"date": "2026-09-29", "fund_code": "588000", "fund_shares": 10.0},
    )
    _package(
        root,
        source_key="SZSE_159915",
        first_observed=first_observed,
        row={"date": "2026-09-29", "fund_code": "159915", "fund_shares": 20.0},
    )
    _package(
        root,
        source_key="SSE_TURNOVER",
        first_observed=first_observed,
        row={"date": "2026-09-29", "sse_a_share_turnover_yuan": 100.0},
    )
    _package(
        root,
        source_key="SZSE_TURNOVER",
        first_observed=first_observed,
        row={"date": "2026-09-29", "szse_a_share_turnover_yuan": 200.0},
    )


def test_load_formal_v3_sources_accepts_exact_early_packages(tmp_path: Path) -> None:
    _all_packages(tmp_path)
    result = load_formal_v3_capital_sources(
        package_root=tmp_path,
        market_session_date="2026-09-29",
        decision_date="2026-09-30",
    )
    assert set(result.provenance) == {
        "SSE_588000",
        "SZSE_159915",
        "SSE_TURNOVER",
        "SZSE_TURNOVER",
    }
    assert float(result.combined_turnover.iloc[0]["amount"]) == 300.0


def test_load_formal_v3_sources_rejects_after_cutoff(tmp_path: Path) -> None:
    _all_packages(tmp_path, first_observed="2026-09-30T08:45:01+08:00")
    with pytest.raises(ValueError, match="FORMAL_V3_SOURCE_AFTER_DECISION_CUTOFF"):
        load_formal_v3_capital_sources(
            package_root=tmp_path,
            market_session_date="2026-09-29",
            decision_date="2026-09-30",
        )
