from pathlib import Path

import pytest

from scripts.assemble_v4a_derived_pit import _manifest_shard_identity


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
