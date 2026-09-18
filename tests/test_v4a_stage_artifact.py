from pathlib import Path

import pytest

from tech_sentiment.v4a_stage_artifact import (
    STAGE_RECEIPT_SCHEMA,
    build_stage_receipt,
    verify_stage_receipt,
    write_stage_receipt,
)


def test_stage_receipt_verifies_exact_files_and_identity(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    (root / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (root / "b.json").write_text('{"ok": true}\n', encoding="utf-8")
    payload = build_stage_receipt(
        root=root,
        files=[root / "a.csv", root / "b.json"],
        stage_kind="issuer",
        stage_id="cninfo-0-of-4",
        source_commit="a" * 40,
        start_date="2022-01-04",
        end_date="2026-09-18",
    )
    assert payload["schema_version"] == STAGE_RECEIPT_SCHEMA
    write_stage_receipt(root / "receipt.json", payload)

    verified = verify_stage_receipt(
        root=root,
        receipt_path=root / "receipt.json",
        source_commit="a" * 40,
        stage_kind="issuer",
        stage_id="cninfo-0-of-4",
        start_date="2022-01-04",
        end_date="2026-09-18",
    )
    assert verified["completion_state"] == "COMPLETE_STAGE"


def test_stage_receipt_fails_closed_on_payload_tamper(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    data = root / "data.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    payload = build_stage_receipt(
        root=root,
        files=[data],
        stage_kind="prices",
        stage_id="prices-0-of-4",
        source_commit="b" * 40,
        start_date="2022-01-04",
        end_date="2026-09-18",
    )
    write_stage_receipt(root / "receipt.json", payload)
    data.write_text("x\n2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        verify_stage_receipt(
            root=root,
            receipt_path=root / "receipt.json",
            source_commit="b" * 40,
            stage_kind="prices",
            stage_id="prices-0-of-4",
            start_date="2022-01-04",
            end_date="2026-09-18",
        )


def test_stage_receipt_fails_closed_on_commit_or_scope_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    data = root / "data.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    payload = build_stage_receipt(
        root=root,
        files=[data],
        stage_kind="policy",
        stage_id="policy",
        source_commit="c" * 40,
        start_date="2022-01-04",
        end_date="2026-09-18",
    )
    write_stage_receipt(root / "receipt.json", payload)

    with pytest.raises(ValueError, match="source commit mismatch"):
        verify_stage_receipt(
            root=root,
            receipt_path=root / "receipt.json",
            source_commit="d" * 40,
            stage_kind="policy",
        )
    with pytest.raises(ValueError, match="end date mismatch"):
        verify_stage_receipt(
            root=root,
            receipt_path=root / "receipt.json",
            source_commit="c" * 40,
            stage_kind="policy",
            end_date="2026-09-17",
        )
