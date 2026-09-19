from __future__ import annotations

import pytest

from tech_sentiment import official_pit_archives
from tech_sentiment.szse_code_migration import (
    fetch_szse_announcements_with_code_migration,
    load_migration_contract,
)


def _item(code: str, published: str, doc_id: str) -> dict[str, object]:
    return {
        "secCode": code,
        "title": f"fixture-{doc_id}",
        "publishTime": published,
        "id": doc_id,
        "attachPath": f"/disc/disk03/finalpage/{doc_id}.PDF",
    }


def test_frozen_szse_migration_contract_preserves_boundaries():
    contract = load_migration_contract()
    assert contract["status"] == "FROZEN_ENGINEERING_IDENTITY_ALIAS"
    assert contract["same_listed_security_identity_only"] is True
    for key in (
        "evidence_source_eligibility_changed",
        "pit_no_lookahead_semantics_changed",
        "research_scope_changed",
        "future_outcomes_used",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        assert contract[key] is False


def test_old_code_accepts_current_alias_but_only_before_effective_date(monkeypatch):
    payload = {
        "announceCount": 2,
        "data": [
            _item("302132", "2025-02-16 16:00:00", "before"),
            _item("302132", "2025-02-17 16:00:00", "after"),
        ],
    }
    monkeypatch.setattr(official_pit_archives, "_post_json", lambda *a, **k: payload)

    frame = fetch_szse_announcements_with_code_migration(
        symbol="300114",
        start_date="2025-02-16",
        end_date="2025-02-17",
    )

    assert frame["symbol"].tolist() == ["300114"]
    assert frame["document_id"].tolist() == ["before"]


def test_new_code_accepts_old_alias_but_only_on_or_after_effective_date(monkeypatch):
    payload = {
        "announceCount": 2,
        "data": [
            _item("300114", "2025-02-16 16:00:00", "before"),
            _item("300114", "2025-02-17 16:00:00", "after"),
        ],
    }
    monkeypatch.setattr(official_pit_archives, "_post_json", lambda *a, **k: payload)

    frame = fetch_szse_announcements_with_code_migration(
        symbol="302132",
        start_date="2025-02-16",
        end_date="2025-02-17",
    )

    assert frame["symbol"].tolist() == ["302132"]
    assert frame["document_id"].tolist() == ["after"]


def test_unrelated_security_identity_still_fails_closed(monkeypatch):
    payload = {
        "announceCount": 1,
        "data": [_item("000001", "2025-02-16 16:00:00", "wrong")],
    }
    monkeypatch.setattr(official_pit_archives, "_post_json", lambda *a, **k: payload)

    with pytest.raises(ValueError, match="security identity drifted"):
        fetch_szse_announcements_with_code_migration(
            symbol="300114",
            start_date="2025-02-16",
            end_date="2025-02-16",
        )
