from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-market-bundle.yml"


def _step_block(text: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = text.index(marker)
    next_step = text.find("\n      - name: ", start + len(marker))
    return text[start:] if next_step == -1 else text[start:next_step]


def test_existing_complete_dated_release_is_reused_before_recomputation() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    reuse_name = "Reuse complete existing immutable dated bundle"
    download_name = "Download point-in-time STAR 50 market data"
    assert text.index(reuse_name) < text.index(download_name)

    reuse = _step_block(text, reuse_name)
    assert 'TAG="market-bundle-${END_DATE}"' in reuse
    assert 'gh release view "${TAG}" --json assets --jq '.assets[].name'' in reuse
    for suffix in ("tar.gz", "sha256", "manifest.json"):
        assert f'"${{TAG}}.{suffix}"' in reuse
    assert 'echo "reuse=true" >> "$GITHUB_OUTPUT"' in reuse
    assert "Reusing complete immutable dated release without recomputation" in reuse

    for name in (
        download_name,
        "Build allowlisted public bundle and deterministic dated identity",
        "Resolve immutable dated bundle identity",
        "Publish latest release assets",
    ):
        block = _step_block(text, name)
        assert "if: steps.immutable_reuse.outputs.reuse != 'true'" in block

    dated = _step_block(text, "Publish or exact-verify immutable dated public bundle")
    assert (
        "if: steps.immutable_reuse.outputs.reuse != 'true' && "
        "steps.dated.outputs.publish == 'true'"
    ) in dated

    non_trading = _step_block(text, "Non-trading target-date receipt")
    assert (
        "if: steps.immutable_reuse.outputs.reuse != 'true' && "
        "steps.dated.outputs.publish != 'true'"
    ) in non_trading

    receipt = _step_block(text, "Existing immutable dated bundle receipt")
    assert "steps.immutable_reuse.outputs.reuse == 'true'" in receipt
    assert "producer recomputation skipped" in receipt


def test_reuse_guard_does_not_weaken_immutable_identity_checks() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    dated = _step_block(text, "Publish or exact-verify immutable dated public bundle")

    assert 'cmp --silent "${CHECKSUM}" "existing/${BASE}.sha256"' in dated
    assert 'cmp --silent "${MANIFEST}" "existing/${BASE}.manifest.json"' in dated
    assert "IMMUTABLE_DATED_BUNDLE_CONFLICT: checksum drift" in dated
    assert "IMMUTABLE_DATED_BUNDLE_CONFLICT: manifest drift" in dated
