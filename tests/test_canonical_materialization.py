import pandas as pd

from tech_sentiment.canonical_materialization import (
    canonicalize_frame,
    canonicalize_metadata,
)


def test_runtime_execution_path_does_not_change_canonical_metadata():
    fresh = {
        "state": "QUALIFIED_INPUT",
        "captured_at_utc": "2026-09-17T20:00:00Z",
        "resumed_chunks": 0,
        "executed_chunks": 12,
        "nested": {
            "checkpoint_resumed_queries": 0,
            "checkpoint_executed_queries": 100,
            "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        },
    }
    resumed = {
        "state": "QUALIFIED_INPUT",
        "captured_at_utc": "2026-09-18T20:00:00Z",
        "resumed_chunks": 12,
        "executed_chunks": 0,
        "nested": {
            "checkpoint_resumed_queries": 100,
            "checkpoint_executed_queries": 0,
            "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        },
    }
    assert canonicalize_metadata(fresh) == canonicalize_metadata(resumed)


def test_capture_clock_is_not_a_canonical_evidence_column():
    frame = pd.DataFrame(
        [
            {
                "evidence_id": "x",
                "source_identity": "OFFICIAL",
                "captured_at_utc": "2026-09-17T20:00:00Z",
            }
        ]
    )
    out = canonicalize_frame(frame)
    assert list(out.columns) == ["evidence_id", "source_identity"]
    assert out.loc[0, "evidence_id"] == "x"
