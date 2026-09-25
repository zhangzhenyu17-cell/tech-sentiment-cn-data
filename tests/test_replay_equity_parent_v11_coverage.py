from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import scripts.replay_equity_parent_v11_coverage as replay


def test_assigned_documents_partition_exactly_once() -> None:
    frame = pd.DataFrame(
        {
            "entity_id": [f"{index:06d}.SZ" for index in range(11)],
            "document_id": [str(index) for index in range(11)],
        }
    )
    seen: list[tuple[str, str]] = []
    for shard in range(4):
        part = replay.assigned_documents(
            frame,
            shard_index=shard,
            shard_count=4,
        )
        seen.extend(
            part[["entity_id", "document_id"]].itertuples(index=False, name=None)
        )
    assert sorted(seen) == sorted(
        frame[["entity_id", "document_id"]].itertuples(index=False, name=None)
    )
    assert len(seen) == len(set(seen))


def test_same_timestamp_conflict_is_counted_without_ordering() -> None:
    facts = pd.DataFrame(
        [
            {
                "entity_id": "300001.SZ",
                "period_end": "2024-12-31",
                "evidence_available_date": "2025-04-01",
                "publication_timestamp": "2025-04-01T00:00:00",
                "value": 100.0,
                "unit": "CNY",
            },
            {
                "entity_id": "300001.SZ",
                "period_end": "2024-12-31",
                "evidence_available_date": "2025-04-01",
                "publication_timestamp": "2025-04-01T00:00:00",
                "value": 101.0,
                "unit": "CNY",
            },
        ]
    )
    assert replay._same_timestamp_groups(facts) == {
        "multi_revision_groups": 1,
        "model_input_conflict_groups": 1,
    }


def test_parser_identity_is_exact_v11() -> None:
    assert replay.EXPECTED_PARSER_VERSION == (
        "official-filing-facts-v11-balance-sheet-parent-equity"
    )
    assert replay.FILING_PARSER_VERSION == replay.EXPECTED_PARSER_VERSION


def test_invalid_shard_fails_closed() -> None:
    frame = pd.DataFrame({"entity_id": ["300001.SZ"], "document_id": ["1"]})
    with pytest.raises(ValueError, match="invalid shard identity"):
        replay.assigned_documents(frame, shard_index=4, shard_count=4)
