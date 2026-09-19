import json

import pandas as pd

from tech_sentiment.fundamental_pit_state import (
    CONTRACT_ID,
    classify_complete_accounting_state,
    materialize_fundamental_state_evidence,
)


def _fact(entity, period, fact_type, value, available, doc, filing_title=None):
    unit = "RATIO" if fact_type == "NET_PROFIT_MARGIN" else "CNY"
    row = {
        "entity_id": entity,
        "period_end": period,
        "fact_type": fact_type,
        "value": value,
        "unit": unit,
        "evidence_available_date": available,
        "publication_timestamp": f"{available} 12:00:00",
        "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        "provider": "CNINFO",
        "document_id": doc,
        "revision_id": f"DOCUMENT:{doc}:SHA256:{doc}",
        "document_url": f"https://static.cninfo.com.cn/{doc}.pdf",
        "document_sha256": (doc * 64)[:64],
        "parser_version": "official-filing-facts-v2-scoped-units-revision-time",
    }
    if filing_title is not None:
        row["filing_title"] = filing_title
    return row


def _period(entity, period, available, doc, revenue, profit, cash, margin, filing_title=None):
    return [
        _fact(entity, period, "OPERATING_REVENUE", revenue, available, doc, filing_title),
        _fact(entity, period, "NET_PROFIT_PARENT", profit, available, doc, filing_title),
        _fact(entity, period, "OPERATING_CASH_FLOW_NET", cash, available, doc, filing_title),
        _fact(entity, period, "NET_PROFIT_MARGIN", margin, available, doc, filing_title),
    ]


def _complete(current, prior):
    def rows(values):
        return {
            "OPERATING_REVENUE": {"value": values[0]},
            "NET_PROFIT_PARENT": {"value": values[1]},
            "OPERATING_CASH_FLOW_NET": {"value": values[2]},
            "NET_PROFIT_MARGIN": {"value": values[3]},
        }

    return classify_complete_accounting_state(rows(current), rows(prior))


def test_contract_uses_direction_only_for_pass_fail_watch():
    state, reasons, _ = _complete((110, 12, 15, 0.11), (100, 10, 12, 0.10))
    assert state == "FUNDAMENTAL_PASS"
    assert reasons == ("ALL_CORE_DIMENSIONS_NON_DETERIORATING",)

    state, reasons, _ = _complete((110, -1, 15, -0.01), (100, 10, 12, 0.10))
    assert state == "FUNDAMENTAL_FAIL"
    assert reasons == ("CURRENT_NET_LOSS",)

    state, reasons, _ = _complete((110, 9, 13, 0.08), (100, 10, 12, 0.10))
    assert state == "FUNDAMENTAL_WATCH"
    assert reasons == ("QUALIFIED_MIXED_ACCOUNTING_STATE",)


def test_missing_prior_comparable_is_data_insufficient_not_watch():
    facts = pd.DataFrame(
        _period("600000.SH", "2025-06-30", "2025-08-20", "CUR", 110, 12, 15, 0.11)
    )
    result = materialize_fundamental_state_evidence(facts)
    assert len(result.evidence) == 1
    row = result.evidence.iloc[0]
    assert row["availability_state"] == "DATA_INSUFFICIENT"
    payload = json.loads(row["evidence_payload"])
    assert payload["fundamental_state"] == "DATA_INSUFFICIENT"
    assert payload["missing_requirements"]
    assert result.summary["readiness_state"] == "DATA_INSUFFICIENT"


def test_later_restatement_does_not_rewrite_earlier_state():
    rows = []
    rows += _period("600000.SH", "2024-06-30", "2024-08-20", "PRIOR", 100, 10, 12, 0.10)
    rows += _period("600000.SH", "2025-06-30", "2025-08-20", "CUR", 110, 12, 15, 0.11)
    rows += _period("600000.SH", "2025-06-30", "2025-10-01", "RESTATED", 90, 8, 8, 0.08)
    result = materialize_fundamental_state_evidence(pd.DataFrame(rows))

    states = result.evidence[result.evidence["event_date"].eq(pd.Timestamp("2025-06-30"))]
    early = states[states["evidence_available_date"].eq(pd.Timestamp("2025-08-20"))].iloc[0]
    later = states[states["evidence_available_date"].eq(pd.Timestamp("2025-10-01"))].iloc[0]
    assert json.loads(early["evidence_payload"])["fundamental_state"] == "FUNDAMENTAL_PASS"
    assert json.loads(later["evidence_payload"])["fundamental_state"] == "FUNDAMENTAL_FAIL"
    assert early["revision_id"] != later["revision_id"]
    assert CONTRACT_ID in early["revision_id"]


def test_same_timestamp_revision_title_is_available_to_fundamental_state():
    rows = []
    rows += _period(
        "600000.SH", "2024-06-30", "2024-08-20", "PRIOR",
        100, 10, 12, 0.10, "2024年半年度报告"
    )
    rows += _period(
        "600000.SH", "2025-06-30", "2025-08-20", "ORIGINAL",
        90, 8, 8, 0.08, "2025年半年度报告"
    )
    rows += _period(
        "600000.SH", "2025-06-30", "2025-08-20", "REVISION",
        110, 12, 15, 0.11, "2025年半年度报告（修订版）"
    )
    result = materialize_fundamental_state_evidence(pd.DataFrame(rows))
    usable = result.evidence[
        result.evidence["availability_state"].eq("HISTORICAL_RECONSTRUCTABLE")
        & result.evidence["event_date"].eq(pd.Timestamp("2025-06-30"))
    ]
    row = usable.iloc[-1]
    payload = json.loads(row["evidence_payload"])
    assert payload["fundamental_state"] == "FUNDAMENTAL_PASS"
    assert {item["document_id"] for item in payload["support"]} >= {"PRIOR", "REVISION"}
    provenance = json.loads(row["provenance"])
    assert "EXPLICIT_REVISION_TITLE" in provenance["selection_semantics"]


def test_no_market_or_outcome_fields_enter_provenance():
    rows = []
    rows += _period("600000.SH", "2024-06-30", "2024-08-20", "PRIOR", 100, 10, 12, 0.10)
    rows += _period("600000.SH", "2025-06-30", "2025-08-20", "CUR", 110, 12, 15, 0.11)
    result = materialize_fundamental_state_evidence(pd.DataFrame(rows))
    usable = result.evidence[result.evidence["availability_state"].eq("HISTORICAL_RECONSTRUCTABLE")]
    provenance = json.loads(usable.iloc[-1]["provenance"])
    assert provenance["future_prices_or_returns_used"] is False
    assert provenance["parameter_search_used"] is False
    assert result.summary["threshold_policy"] == "SIGN_AND_EXACT_DIRECTION_ONLY_NO_RETURN_OPTIMIZATION"
