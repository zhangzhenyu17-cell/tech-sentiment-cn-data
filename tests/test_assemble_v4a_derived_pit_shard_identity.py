import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_v4a_derived_pit.py"
_SPEC = importlib.util.spec_from_file_location("assemble_v4a_derived_pit_test_module", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_manifest_shard_identity = _MODULE._manifest_shard_identity
_prepare_checkpoint = _MODULE._prepare_checkpoint
_mark_phase = _MODULE._mark_phase
_phase_done = _MODULE._phase_done


def test_manifest_shard_identity_accepts_zero_index():
    count, index = _manifest_shard_identity(
        {"shard_count": 4, "shard_index": 0},
        kind="fundamental_earnings",
        stage_dir=Path("stage/fundamental/0"),
    )
    assert (count, index) == (4, 0)


@pytest.mark.parametrize(
    "manifest",
    [
        {"shard_count": 4},
        {"shard_index": 0},
        {"shard_count": 0, "shard_index": 0},
        {"shard_count": 4, "shard_index": -1},
        {"shard_count": 4, "shard_index": 4},
        {"shard_count": True, "shard_index": 0},
        {"shard_count": 4, "shard_index": False},
    ],
)
def test_manifest_shard_identity_rejects_missing_or_invalid_values(manifest):
    with pytest.raises(ValueError, match="invalid shard identity"):
        _manifest_shard_identity(
            manifest,
            kind="prices",
            stage_dir=Path("stage/prices/0"),
        )


def test_derived_checkpoint_state_is_identity_strict_and_phase_durable(tmp_path):
    identity = {
        "schema_version": "v4a-derived-phase-checkpoint-v1",
        "start_date": "2022-01-04",
        "end_date": "2026-09-17",
        "symbols_sha256": "a" * 64,
    }
    state = _prepare_checkpoint(tmp_path, identity)
    assert not _phase_done(state, "fundamental")

    (tmp_path / "fundamental").mkdir()
    (tmp_path / "fundamental" / "partial.csv").write_text("x\n1\n", encoding="utf-8")
    state = _prepare_checkpoint(tmp_path, identity)
    assert not _phase_done(state, "fundamental")

    _mark_phase(tmp_path, state, "fundamental")
    restored = _prepare_checkpoint(tmp_path, identity)
    assert _phase_done(restored, "fundamental")

    mismatched = dict(identity)
    mismatched["symbols_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="checkpoint identity mismatch"):
        _prepare_checkpoint(tmp_path, mismatched)
