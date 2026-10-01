from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from tech_sentiment import cross_sector_fundamental_date_increment_v1 as inc

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/cross_sector_fundamental_date_increment_v1.json"
SHA = "a" * 40
CALENDAR = ["2026-09-29", "2026-09-30"]
RAW_COLUMNS = ["代码", "简称", "公告标题", "公告时间", "公告链接", "公告附件链接"]


def _contract():
    return inc.load_contract(ROOT, CONTRACT)


def _row(document, when, title="2026年半年度报告"):
    return {"代码": "000032", "简称": "test", "公告标题": title, "公告时间": when,
            "公告链接": f"https://www.cninfo.com.cn/new/disclosure/detail?announcementId={document}",
            "公告附件链接": f"https://static.cninfo.com.cn/finalpage/2026-09-30/{document}.PDF"}


def _parser(**kwargs):
    return pd.DataFrame([{**{k: kwargs[k] for k in ("entity_id", "evidence_available_date", "publication_timestamp", "source_identity", "provider", "document_id", "revision_id", "document_url", "document_sha256")},
                          "period_end": "2026-06-30", "fact_type": fact, "value": 1.0,
                          "unit": "RATIO" if fact == "NET_PROFIT_MARGIN" else "CNY", "parser_version": inc.FILING_PARSER_VERSION}
                         for fact in ("OPERATING_REVENUE", "NET_PROFIT_PARENT", "OPERATING_CASH_FLOW_NET", "NET_PROFIT_MARGIN")])


def _providers(monkeypatch, rows):
    calls = {"query": [], "document": []}
    def query(**kwargs):
        calls["query"].append(kwargs)
        return pd.DataFrame(rows, columns=RAW_COLUMNS)
    def download(url):
        calls["document"].append(url)
        return SimpleNamespace(sha256="b" * 64, url=url, retrieval_url=url, content=b"%PDF-fixture")
    monkeypatch.setattr(inc, "fetch_cninfo_announcements_direct", query)
    monkeypatch.setattr(inc, "download_official_document", download)
    monkeypatch.setattr(inc, "extract_pdf_text", lambda content: "fixture text")
    monkeypatch.setattr(inc, "classify_official_filing_presentation", lambda text: "COMPLETE_OR_CANONICAL")
    monkeypatch.setattr(inc, "build_filing_fact_rows", _parser)
    return calls


def _capture(tmp_path, symbols=None):
    return inc.capture_increment(symbols=symbols or ["000032"], contract=_contract(), trading_dates=CALENDAR,
                                 source_commit=SHA, checkpoint_dir=tmp_path / "checkpoints")


def test_exact_frozen_scope_covers_existing_five_memberships_once():
    scope, members = inc.build_scope(ROOT, _contract())
    assert len(scope) == 572
    assert {name: len(frame) for name, frame in members.items()} == {
        "STAR50": 50, "ChiNext50": 50, "INNOVATION_DRUG": 50, "DEFENSE": 50, "CORE_BETA": 500}
    units = [inc.unit_symbols(scope, index) for index in range(16)]
    assert sorted(symbol for unit in units for symbol in unit) == sorted(scope["symbol"])
    assert max(map(len, units)) == 36


def test_membership_hash_rejects_text_transfer_extra_final_newline(tmp_path):
    contract = _contract()
    for spec in contract["memberships"].values():
        for key in ("path", "anchor_path", "adjustments_path"):
            if key in spec:
                target = tmp_path / spec[key]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / spec[key]).read_bytes())
    assert len(inc.build_scope(tmp_path, contract)[0]) == 572
    path = tmp_path / contract["memberships"]["STAR50"]["anchor_path"]
    original = path.read_bytes()
    changed = original + b"\n"
    assert changed != original
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="membership digest"):
        inc.build_scope(tmp_path, contract)


def test_after_close_prior_session_is_captured_target_after_close_is_excluded(tmp_path, monkeypatch):
    calls = _providers(monkeypatch, [_row("100", "2026-09-29 16:00:00"), _row("101", "2026-09-29 14:00:00"),
        _row("102", "2026-09-30 15:00:00"), _row("103", "2026-09-30 15:00:01"), _row("104", "2026-09-30")])
    result = _capture(tmp_path)
    assert result["complete"] is True
    assert set(result["facts"]["document_id"]) == {"100", "102"}
    assert pd.to_datetime(result["facts"]["evidence_available_date"]).eq(pd.Timestamp("2026-09-30")).all()
    assert sum(result["documents"]["state"].eq("NOT_AVAILABLE_BY_TARGET_CLOSE")) == 2
    assert len(calls["document"]) == 2
    assert calls["query"] == [{"symbol": "000032", "start_date": "2026-09-29", "end_date": "2026-09-30"}]


def test_replay_resumes_queries_and_documents_without_provider_recomputation(tmp_path, monkeypatch):
    calls = _providers(monkeypatch, [_row("100", "2026-09-29 16:00:00")])
    first = _capture(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("provider recomputation")
    monkeypatch.setattr(inc, "fetch_cninfo_announcements_direct", forbidden)
    monkeypatch.setattr(inc, "download_official_document", forbidden)
    second = _capture(tmp_path)
    assert second["complete"] is True
    assert second["counters"] == {"executed_queries": 0, "resumed_queries": 1, "executed_documents": 0, "resumed_documents": 1}
    pd.testing.assert_frame_equal(first["facts"].astype(str), second["facts"].astype(str))


def test_provider_failure_is_incomplete_and_trips_bounded_breaker(tmp_path, monkeypatch):
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("CNINFO protocol failed")
    monkeypatch.setattr(inc, "fetch_cninfo_announcements_direct", fail)
    result = _capture(tmp_path, ["000032", "000035", "000063", "000069", "000157"])
    assert result["complete"] is False and result["breaker_fired"] is True
    assert len(calls) == 3
    assert result["entities"]["query_status"].eq("QUERY_FAILED").all()
    assert result["errors"]["severity"].eq("HARD_FAILURE").all()
    assert result["facts"].empty


def test_missing_attachment_is_hard_failure_without_old_state_fallback(tmp_path, monkeypatch):
    row = _row("100", "2026-09-29 16:00:00")
    row["公告附件链接"] = None
    _providers(monkeypatch, [row])
    result = _capture(tmp_path)
    assert result["complete"] is False
    assert result["entities"].iloc[0]["hard_failures"] == 1
    assert result["facts"].empty


def test_soft_parse_gap_is_explicit_and_never_becomes_fundamental_state(tmp_path, monkeypatch):
    _providers(monkeypatch, [_row("100", "2026-09-29 16:00:00")])
    def no_text(*args, **kwargs):
        raise ValueError("official filing has no extractable text layer")
    monkeypatch.setattr(inc, "extract_pdf_text", no_text)
    result = _capture(tmp_path)
    assert result["complete"] is True and result["facts"].empty
    assert result["entities"].iloc[0]["soft_parse_gaps"] == 1
    assert result["documents"].iloc[0]["state"] == "SOFT_DATA_INSUFFICIENCY"
    manifest = inc.write_capture(result, tmp_path / "out", source_commit=SHA, contract_sha256="c" * 64,
        scope_sha256="d" * 64, producer_sha256="e" * 64, symbols=["000032"], unit_index=0)
    assert all(manifest[flag] is False for flag in inc.FALSE_FLAGS)
    assert manifest["soft_parse_gaps"] == 1


def test_complete_empty_query_proves_no_increment_without_inventing_fact_rows(tmp_path, monkeypatch):
    calls = _providers(monkeypatch, [])
    result = _capture(tmp_path)
    assert result["complete"] is True and result["facts"].empty
    assert result["entities"].iloc[0]["query_status"] == "EXACT_INCREMENT_QUERY_COMPLETE"
    assert not calls["document"]


def _cli():
    spec = importlib.util.spec_from_file_location("increment_cli", ROOT / "scripts/cross_sector_fundamental_date_increment_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    pd.DataFrame({"date": CALENDAR}).to_csv(shared / "trading_calendar.csv", index=False)
    assert _cli().main(["prepare", "--root", str(ROOT), "--source-commit", SHA, "--shared-dir", str(shared)]) == 0
    payload = json.loads((shared / "shared_manifest.json").read_text())
    common = {key: payload[key] for key in ("source_commit", "contract_sha256", "scope_sha256", "producer_sha256")}
    scope = pd.read_csv(shared / "scope.csv", dtype={"symbol": str})
    return shared, common, scope


def _empty_unit(symbols):
    return {"facts": pd.DataFrame(columns=(*inc.FILING_FACT_COLUMNS, "document_presentation_variant", "filing_title")),
        "entities": pd.DataFrame([{ "entity_id": inc._entity_id(symbol), "symbol": symbol,
            "query_status": "EXACT_INCREMENT_QUERY_COMPLETE", "numeric_documents": 0, "parsed_documents": 0,
            "soft_parse_gaps": 0, "hard_failures": 0} for symbol in symbols], columns=inc.ENTITY_COLUMNS),
        "documents": pd.DataFrame(columns=inc.DOCUMENT_COLUMNS), "errors": pd.DataFrame(columns=inc.ERROR_COLUMNS),
        "complete": True, "breaker_fired": False, "counters": {"executed_queries": len(symbols), "resumed_queries": 0, "executed_documents": 0, "resumed_documents": 0}}


def test_real_finalizer_cli_requires_every_unit_and_preserves_authority(tmp_path):
    shared, common, scope = _prepared(tmp_path)
    work, out = tmp_path / "units", tmp_path / "final"
    for index in range(16):
        symbols = inc.unit_symbols(scope, index)
        inc.write_capture(_empty_unit(symbols), work / f"unit-{index}", symbols=symbols, unit_index=index, **common)
    command = [sys.executable, str(ROOT / "scripts/cross_sector_fundamental_date_increment_v1.py"), "finalize",
        "--root", str(ROOT), "--source-commit", SHA, "--shared-dir", str(shared), "--work-unit-root", str(work), "--out-dir", str(out)]
    complete = subprocess.run(command, capture_output=True, text=True)
    assert complete.returncode == 0, complete.stderr
    manifest = json.loads((out / "bundle_manifest.json").read_text())
    assert manifest["scope_unique_entities"] == 572 and manifest["unit_count"] == 16
    assert all(manifest[flag] is False for flag in inc.FALSE_FLAGS)
    assert len(pd.read_csv(out / "entity_window_receipts.csv")) == 572
    assert pd.read_csv(out / "versioned_filing_facts.csv").empty
    (work / "unit-15" / "stage_manifest.json").unlink()
    incomplete = subprocess.run(command, capture_output=True, text=True)
    assert incomplete.returncode != 0 and "missing immutable work unit" in incomplete.stderr


def test_actual_capture_cli_reuses_verified_unit_without_network(tmp_path, monkeypatch):
    shared, common, scope = _prepared(tmp_path)
    symbols = inc.unit_symbols(scope, 0)
    previous = tmp_path / "resume" / "original"
    inc.write_capture(_empty_unit(symbols), previous, symbols=symbols, unit_index=0, **common)
    cli = _cli()
    monkeypatch.setattr(cli, "capture_increment", lambda **kwargs: pytest.fail("immutable unit was recomputed"))
    args = ["capture", "--root", str(ROOT), "--source-commit", SHA, "--shared-dir", str(shared), "--unit-index", "0",
            "--checkpoint-dir", str(tmp_path / "checkpoints"), "--resume-root", str(tmp_path / "resume"), "--out-dir", str(tmp_path / "unit")]
    assert cli.main(args) == 0
    manifest = json.loads((previous / "stage_manifest.json").read_text())
    manifest["private_qualification_granted"] = True
    (previous / "stage_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="private_qualification"):
        cli.main(args)


def test_actual_pilot_and_capture_cli_share_exact_query_checkpoints(tmp_path, monkeypatch):
    shared, common, scope = _prepared(tmp_path)
    calls = _providers(monkeypatch, [])
    cli = _cli()
    monkeypatch.setattr(cli, "probe_document", lambda spec: {"document_id": spec["document_id"], "document_sha256": spec["document_sha256"]})
    assert cli.main(["pilot", "--root", str(ROOT), "--source-commit", SHA, "--shared-dir", str(shared),
                     "--checkpoint-dir", str(shared / "pilot_checkpoint")]) == 0
    assert len(calls["query"]) == 5
    assert cli.main(["capture", "--root", str(ROOT), "--source-commit", SHA, "--shared-dir", str(shared),
        "--unit-index", "0", "--checkpoint-dir", str(tmp_path / "progress"), "--out-dir", str(tmp_path / "unit")]) == 0
    manifest = json.loads((tmp_path / "unit" / "stage_manifest.json").read_text())
    assert manifest["counters"]["resumed_queries"] >= 1
    assert manifest["counters"]["executed_queries"] + manifest["counters"]["resumed_queries"] == len(inc.unit_symbols(scope, 0))
    assert manifest["complete"] is True


def test_pilot_proves_exact_document_digest_before_parser(tmp_path, monkeypatch):
    _providers(monkeypatch, [])
    spec = dict(_contract()["pilot_document"], document_sha256="b" * 64)
    assert inc.probe_document(spec)["document_sha256"] == "b" * 64
    spec["document_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="document SHA"):
        inc.probe_document(spec)


@pytest.mark.parametrize("mutation,match", [("digest", "file digest"), ("scope", "entity completeness"), ("accounting", "document accounting")])
def test_immutable_unit_rejects_content_and_accounting_drift(tmp_path, mutation, match):
    shared, common, scope = _prepared(tmp_path)
    symbols = inc.unit_symbols(scope, 0)
    unit = tmp_path / "unit"
    inc.write_capture(_empty_unit(symbols), unit, symbols=symbols, unit_index=0, **common)
    path = unit / "entity_window_receipts.csv"
    if mutation == "digest":
        path.write_text(path.read_text() + "\n")
    else:
        frame = pd.read_csv(path, dtype={"symbol": str})
        if mutation == "scope": frame = frame.iloc[1:]
        else: frame.loc[0, "numeric_documents"] = 1
        frame.to_csv(path, index=False)
        manifest = json.loads((unit / "stage_manifest.json").read_text())
        manifest["file_sha256"][path.name] = sha256(path.read_bytes()).hexdigest()
        (unit / "stage_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=match):
        inc.validate_capture(unit, symbols=symbols, unit_index=0, **common)


def test_workflow_is_manual_bounded_and_never_restarts_old_shards():
    text = (ROOT / ".github/workflows/cross-sector-fundamental-date-increment-v1.yml").read_text()
    assert "workflow_dispatch:" in text and "max-parallel: 4" in text
    assert "schedule:" not in text and "workflow_run:" not in text and "pull_request:" not in text
    assert "cancel-in-progress: false" in text
    assert "materialize_cross_sector_current_fundamental_shard" not in text
    assert "materialize_cross_sector_fundamental_expansion_shard" not in text
    assert "stage/final/versioned_filing_facts.csv" in text
