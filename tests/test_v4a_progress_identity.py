from pathlib import Path
import shutil

import pytest

from tech_sentiment.v4a_persistent_stage import (
    progress_semantic_files,
    progress_semantic_fingerprint,
)


@pytest.mark.parametrize(
    "family",
    (
        "capital",
        "financing",
        "issuer_cninfo",
        "issuer_sse",
        "issuer_szse",
        "prices",
        "policy",
        "derived",
    ),
)
def test_progress_identity_excludes_workflow_and_post_materialization_orchestration(family):
    paths = {
        path.relative_to(Path.cwd()).as_posix()
        for path in progress_semantic_files(".", family)
    }
    assert "pyproject.toml" in paths
    assert not any(path.startswith(".github/workflows/") for path in paths)
    assert "scripts/assert_v4a_stage_qualifiable.py" not in paths
    assert "scripts/write_v4a_stage_receipt.py" not in paths
    assert "scripts/v4a_persistent_stage_bundle.py" not in paths


def test_progress_identity_keeps_semantic_reference_inputs():
    szse = {
        path.relative_to(Path.cwd()).as_posix()
        for path in progress_semantic_files(".", "issuer_szse")
    }
    derived = {
        path.relative_to(Path.cwd()).as_posix()
        for path in progress_semantic_files(".", "derived")
    }
    assert "reference/v4a_szse_security_code_migration_contract_v1.json" in szse
    assert "reference/v4a_fundamental_pit_state_contract_v1.json" in derived
    assert "reference/v4a_issuer_source_aggregate_compatibility_bridge_v1.json" not in derived


def test_progress_fingerprint_survives_workflow_only_change_but_not_semantic_change(tmp_path):
    family = "capital"
    root = Path.cwd()
    for source in progress_semantic_files(root, family):
        relative = source.relative_to(root)
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    before = progress_semantic_fingerprint(tmp_path, family)[
        "progress_semantic_fingerprint"
    ]

    workflow = tmp_path / ".github/workflows/v4a-capital.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("timeout-minutes: 360\n", encoding="utf-8")
    after_workflow = progress_semantic_fingerprint(tmp_path, family)[
        "progress_semantic_fingerprint"
    ]
    assert after_workflow == before

    materializer = tmp_path / "scripts/qualify_capital_inputs.py"
    materializer.write_text(
        materializer.read_text(encoding="utf-8") + "\n# semantic fingerprint test\n",
        encoding="utf-8",
    )
    after_semantic = progress_semantic_fingerprint(tmp_path, family)[
        "progress_semantic_fingerprint"
    ]
    assert after_semantic != before
