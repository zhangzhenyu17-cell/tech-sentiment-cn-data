from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/preflight_edge1_exchange_earnings_transport.py"
SPEC = importlib.util.spec_from_file_location(
    "preflight_edge1_exchange_earnings_transport",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _issuer_rows() -> pd.DataFrame:
    rows = []
    for source, suffix, prefix in (
        ("SSE_ANNOUNCEMENT_ARCHIVE", ".SH", "688"),
        ("SZSE_ANNOUNCEMENT_ARCHIVE", ".SZ", "300"),
    ):
        for idx in range(3):
            rows.append(
                {
                    "source_identity": source,
                    "evidence_type": "ISSUER_EARNINGS_FORECAST",
                    "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                    "entity_id": f"{prefix}{idx:03d}{suffix}",
                    "document_id": f"{source}-{idx}",
                    "evidence_available_date": f"2026-01-0{idx + 1}",
                    "source_url_identity": (
                        f"https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
                        f"2026-01-0{idx + 1}/{prefix}{idx:03d}_fixture.pdf"
                        if source == "SSE_ANNOUNCEMENT_ARCHIVE"
                        else f"https://disc.static.szse.cn/download/{prefix}{idx:03d}_fixture.PDF"
                    ),
                }
            )
    return pd.DataFrame(rows)


def test_live_preflight_selects_two_distinct_entities_per_provider() -> None:
    selected = MODULE.select_representative_rows(_issuer_rows())
    assert len(selected) == 4
    counts = selected.groupby("source_identity")["entity_id"].nunique().to_dict()
    assert counts == {
        "SSE_ANNOUNCEMENT_ARCHIVE": 2,
        "SZSE_ANNOUNCEMENT_ARCHIVE": 2,
    }


def test_live_preflight_treats_unclassified_as_transport_success(monkeypatch) -> None:
    def fake_materialize(frame: pd.DataFrame):
        return SimpleNamespace(
            evidence=pd.DataFrame(),
            unclassified=pd.DataFrame([{"reason": "UNKNOWN"}]),
            errors=pd.DataFrame(),
        )

    monkeypatch.setattr(
        MODULE,
        "materialize_registered_exchange_earnings_directions",
        fake_materialize,
    )
    receipt = MODULE.run_live_preflight(_issuer_rows())
    assert receipt["status"] == "LIVE_PROVIDER_DOCUMENT_PREFLIGHT_PASS"
    assert receipt["selected_documents"] == 4
    assert receipt["hard_error_count"] == 0
    assert all(row["status"] == "UNCLASSIFIED" for row in receipt["results"])
    assert receipt["formal_evidence_handoff"] is False
    assert receipt["evidence_qualification_changed"] is False
    assert receipt["outcomes_read"] is False


def test_live_preflight_fails_closed_on_transport_error(monkeypatch) -> None:
    def fake_materialize(frame: pd.DataFrame):
        return SimpleNamespace(
            evidence=pd.DataFrame(),
            unclassified=pd.DataFrame(),
            errors=pd.DataFrame(
                [{"error": "HTML challenge returned instead of PDF"}]
            ),
        )

    monkeypatch.setattr(
        MODULE,
        "materialize_registered_exchange_earnings_directions",
        fake_materialize,
    )
    receipt = MODULE.run_live_preflight(_issuer_rows())
    assert receipt["status"] == "LIVE_PROVIDER_DOCUMENT_PREFLIGHT_FAIL"
    assert receipt["hard_error_count"] == 4
    assert all(row["status"] == "ERROR" for row in receipt["results"])
    assert all(row["canonical_document_url"] for row in receipt["results"])
