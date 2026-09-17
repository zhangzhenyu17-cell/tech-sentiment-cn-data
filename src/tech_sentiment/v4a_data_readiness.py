from __future__ import annotations

from typing import Mapping


ALLOWED_STATES = {
    "QUALIFIED_INPUT",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
    "FORWARD_ONLY",
    "DATA_INSUFFICIENT",
    "UNAVAILABLE",
}
_BLOCKER_PRECEDENCE = (
    "UNAVAILABLE",
    "DATA_INSUFFICIENT",
    "FORWARD_ONLY",
    "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "PARTIAL_COVERAGE",
)


def _state(value: object, default: str = "DATA_INSUFFICIENT") -> str:
    text = str(value or "").strip().upper()
    return text if text in ALLOWED_STATES else default


def _combine(values: list[str]) -> str:
    normalized = [_state(value) for value in values]
    if normalized and all(value == "QUALIFIED_INPUT" for value in normalized):
        return "QUALIFIED_INPUT"
    for blocker in _BLOCKER_PRECEDENCE:
        if blocker in normalized:
            return blocker
    return "DATA_INSUFFICIENT"


def build_v4a_data_readiness(
    *,
    capital_summary: Mapping[str, object],
    financing_summary: Mapping[str, object] | None,
    pit_summary: Mapping[str, object] | None,
) -> dict[str, str]:
    etf_state = _state(capital_summary.get("etf_readiness_state"))
    turnover_state = _state(capital_summary.get("turnover_readiness_state"))

    financing_state = "DATA_INSUFFICIENT"
    if financing_summary is not None:
        qualification = str(financing_summary.get("qualification_state") or "").upper()
        coverage = float(financing_summary.get("bilateral_coverage") or 0.0)
        if qualification in {"CANONICAL_UNIT_QUALIFIED", "QUALIFIED_INPUT"} and coverage == 1.0:
            financing_state = "QUALIFIED_INPUT"
        elif coverage > 0:
            financing_state = "PARTIAL_COVERAGE"

    pit = dict(pit_summary or {})
    raw_states = pit.get("source_states")
    sources = dict(raw_states) if isinstance(raw_states, Mapping) else {}
    cninfo = _state(sources.get("CNINFO_ANNOUNCEMENT_ARCHIVE"))
    sse = _state(sources.get("SSE_ANNOUNCEMENT_ARCHIVE"))
    szse = _state(sources.get("SZSE_ANNOUNCEMENT_ARCHIVE"))
    fundamental_derived = _state(sources.get("DERIVED_PIT_FUNDAMENTAL_TRENDS"))
    valuation = _state(sources.get("DERIVED_PIT_TRAILING_VALUATION"))
    policy = _state(sources.get("OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"))
    earnings_direction = _state(pit.get("earnings_direction_readiness_state"))

    fundamental_state = _combine([cninfo, sse, szse, fundamental_derived])
    earnings_state = _combine([cninfo, sse, szse, earnings_direction])
    major_event_state = _combine([cninfo, sse, szse, policy])
    major_negative_state = (
        "QUALIFIED_INPUT"
        if pit.get("major_negative_event_exclusion_complete") is True
        else "DATA_INSUFFICIENT"
    )

    audit_raw = pit.get("pit_audit")
    audit = dict(audit_raw) if isinstance(audit_raw, Mapping) else {}
    audit_required = (
        "required_fields_complete",
        "no_future_evidence",
        "duplicate_identity_free",
        "provenance_complete",
        "prefix_replay_filter_equality",
        "as_of_replay_equality",
        "revision_identity_complete",
        "later_revision_does_not_rewrite_prior_rows",
    )
    audit_passed = all(audit.get(field) is True for field in audit_required)
    clean = (
        etf_state == "QUALIFIED_INPUT"
        and turnover_state == "QUALIFIED_INPUT"
        and fundamental_state == "QUALIFIED_INPUT"
        and earnings_state == "QUALIFIED_INPUT"
        and valuation == "QUALIFIED_INPUT"
        and major_event_state == "QUALIFIED_INPUT"
        and major_negative_state == "QUALIFIED_INPUT"
        and audit_passed
    )

    return {
        "588000_long_flow": etf_state,
        "sse_szse_a_shares_turnover": turnover_state,
        "financing": financing_state,
        "fundamental_pit": fundamental_state,
        "earnings_pit": earnings_state,
        "valuation_pit": valuation,
        "major_event_pit": major_event_state,
        "major_negative_exclusion": major_negative_state,
        "clean_forward_external_evidence": "QUALIFIED_INPUT" if clean else "DATA_INSUFFICIENT",
    }


__all__ = ["ALLOWED_STATES", "build_v4a_data_readiness"]
