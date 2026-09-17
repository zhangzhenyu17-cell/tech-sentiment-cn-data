from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.immutable_checkpoint import (
    CheckpointIdentity,
    ImmutableCheckpointStore,
)


def _identity(*, commit: str = "abc", end: str = "2026-09-18") -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="capital-chunk",
        producer_version="v1",
        source_commit=commit,
        source_identities=("SSE_ETF_SCALE_DAILY",),
        query_identity={"fund_code": "588000"},
        scope={"start_date": "2026-01-01", "end_date": end},
    )


def test_checkpoint_requires_exact_code_query_and_scope_identity(tmp_path: Path):
    store = ImmutableCheckpointStore(tmp_path)
    frame = pd.DataFrame({"date": ["2026-01-05"], "value": [1.0]})
    store.save(_identity(), frames={"data": frame})
    assert store.load(_identity()) is not None
    assert store.load(_identity(commit="different")) is None
    assert store.load(_identity(end="2026-09-19")) is None


def test_checkpoint_detects_mutated_chunk_bytes(tmp_path: Path):
    store = ImmutableCheckpointStore(tmp_path)
    identity = _identity()
    store.save(identity, frames={"data": pd.DataFrame({"x": [1, 2]})})
    data_path = tmp_path / identity.fingerprint / "data.csv"
    data_path.write_text("x\n999\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load(identity)


def test_fresh_and_resumed_chunk_outputs_are_identical(tmp_path: Path):
    store = ImmutableCheckpointStore(tmp_path)
    identity = _identity()
    fresh = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-05", periods=3, freq="D"),
            "value": [1.0, 2.0, 3.0],
        }
    )
    receipt = store.save(identity, frames={"canonical": fresh})
    loaded = store.load(identity)
    assert loaded is not None
    resumed = loaded.frames["canonical"]
    expected = fresh.copy()
    expected["date"] = expected["date"].dt.strftime("%Y-%m-%d")
    assert resumed.equals(expected)
    assert loaded.receipt["receipt_sha256"] == receipt["receipt_sha256"]
