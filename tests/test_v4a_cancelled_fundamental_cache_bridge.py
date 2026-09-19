from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from tech_sentiment.earnings_materialization import EARNINGS_MATERIALIZER_VERSION
from tech_sentiment.filing_materialization import (
    FILING_PARSER_VERSION,
    LEGACY_FILING_PARSER_VERSION,
)
from tech_sentiment.immutable_checkpoint import (
    CheckpointIdentity,
    ImmutableCheckpointStore,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_v4a_cancelled_fundamental_cache.py"
SPEC = importlib.util.spec_from_file_location("cancelled_bridge_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _identity(
    *,
    producer: str,
    version: str,
    commit: str,
    symbol: str,
    suffix: str,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer=producer,
        producer_version=version,
        source_commit=commit,
        source_identities=("CNINFO_ANNOUNCEMENT_ARCHIVE",),
        query_identity={
            "symbol": symbol,
            "document_id": suffix,
            "attachment_url": f"https://example.invalid/{suffix}.PDF",
        },
        scope={"document_id": suffix},
    )


def test_cancelled_bridge_migrates_only_valid_current_checkpoints_for_unit(tmp_path: Path):
    source = ImmutableCheckpointStore(tmp_path / "source")
    destination = ImmutableCheckpointStore(tmp_path / "destination")
    progress_commit = "2" * 40

    symbols_csv = tmp_path / "symbols.csv"
    pd.DataFrame({"symbol": ["000001", "000002", "000003", "000004"]}).to_csv(
        symbols_csv, index=False
    )

    current_filing = _identity(
        producer="official-filing-facts",
        version=FILING_PARSER_VERSION,
        commit=progress_commit,
        symbol="000001",
        suffix="filing-current",
    )
    current_earnings = _identity(
        producer="issuer-earnings-direction-document",
        version=EARNINGS_MATERIALIZER_VERSION,
        commit=progress_commit,
        symbol="000001",
        suffix="earnings-current",
    )
    other_unit = _identity(
        producer="official-filing-facts",
        version=FILING_PARSER_VERSION,
        commit=progress_commit,
        symbol="000002",
        suffix="other-unit",
    )
    legacy = _identity(
        producer="official-filing-facts",
        version=LEGACY_FILING_PARSER_VERSION,
        commit="1" * 40,
        symbol="000001",
        suffix="legacy",
    )
    wrong_commit = _identity(
        producer="official-filing-facts",
        version=FILING_PARSER_VERSION,
        commit="3" * 40,
        symbol="000001",
        suffix="wrong-commit",
    )

    for identity in (
        current_filing,
        current_earnings,
        other_unit,
        legacy,
        wrong_commit,
    ):
        source.save(
            identity,
            frames={"facts": pd.DataFrame([{"value": 1}])},
            metadata={"fixture": True},
        )

    result = MODULE.migrate(
        source_root=tmp_path / "source",
        destination_root=tmp_path / "destination",
        symbols_csv=symbols_csv,
        unit_index=0,
        unit_count=2,
        progress_source_commit=progress_commit,
    )

    # Unit 0 gets sorted positions 0 and 2 => 000001, 000003.
    assert result["eligible_current_v9_checkpoints"] == 2
    assert result["copied_checkpoints"] == 2
    assert result["formal_evidence_handoff"] is False
    assert result["qualification_granted"] is False

    assert destination.load(current_filing) is not None
    assert destination.load(current_earnings) is not None
    assert destination.load(other_unit) is None
    assert destination.load(legacy) is None
    assert destination.load(wrong_commit) is None


def test_cancelled_bridge_is_idempotent_and_revalidates_existing_destination(tmp_path: Path):
    source = ImmutableCheckpointStore(tmp_path / "source")
    progress_commit = "2" * 40
    symbols_csv = tmp_path / "symbols.csv"
    pd.DataFrame({"symbol": ["000001"]}).to_csv(symbols_csv, index=False)

    identity = _identity(
        producer="official-filing-facts",
        version=FILING_PARSER_VERSION,
        commit=progress_commit,
        symbol="000001",
        suffix="current",
    )
    source.save(
        identity,
        frames={"facts": pd.DataFrame([{"value": 1}])},
    )

    first = MODULE.migrate(
        source_root=tmp_path / "source",
        destination_root=tmp_path / "destination",
        symbols_csv=symbols_csv,
        unit_index=0,
        unit_count=1,
        progress_source_commit=progress_commit,
    )
    second = MODULE.migrate(
        source_root=tmp_path / "source",
        destination_root=tmp_path / "destination",
        symbols_csv=symbols_csv,
        unit_index=0,
        unit_count=1,
        progress_source_commit=progress_commit,
    )

    assert first["copied_checkpoints"] == 1
    assert second["copied_checkpoints"] == 0
    assert second["already_present_checkpoints"] == 1
