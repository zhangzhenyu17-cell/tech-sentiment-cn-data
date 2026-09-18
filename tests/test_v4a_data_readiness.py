from tech_sentiment.v4a_data_readiness import build_v4a_data_readiness


def _pit(**overrides):
    payload = {
        "source_states": {
            "CNINFO_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
            "SSE_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
            "SZSE_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
            "DERIVED_PIT_FUNDAMENTAL_TRENDS": "QUALIFIED_INPUT",
            "DERIVED_PIT_TRAILING_VALUATION": "QUALIFIED_INPUT",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE": "QUALIFIED_INPUT",
        },
        "earnings_direction_readiness_state": "QUALIFIED_INPUT",
        "major_negative_event_exclusion_complete": True,
        "pit_audit": {
            "required_fields_complete": True,
            "no_future_evidence": True,
            "duplicate_identity_free": True,
            "provenance_complete": True,
            "prefix_replay_filter_equality": True,
            "as_of_replay_equality": True,
            "revision_identity_complete": True,
            "later_revision_does_not_rewrite_prior_rows": True,
        },
    }
    payload.update(overrides)
    return payload


def _capital(**overrides):
    payload = {
        "etf_readiness_state": "QUALIFIED_INPUT",
        "turnover_readiness_state": "QUALIFIED_INPUT",
    }
    payload.update(overrides)
    return payload


def _financing(**overrides):
    payload = {
        "qualification_state": "CANONICAL_UNIT_QUALIFIED",
        "bilateral_coverage": 1.0,
    }
    payload.update(overrides)
    return payload


def test_all_nine_items_can_be_data_level_qualified():
    matrix = build_v4a_data_readiness(
        capital_summary=_capital(),
        financing_summary=_financing(),
        pit_summary=_pit(),
    )
    assert set(matrix) == {
        "588000_long_flow",
        "sse_szse_a_shares_turnover",
        "financing",
        "fundamental_pit",
        "earnings_pit",
        "valuation_pit",
        "major_event_pit",
        "major_negative_exclusion",
        "clean_forward_external_evidence",
    }
    assert all(value == "QUALIFIED_INPUT" for value in matrix.values())


def test_unknown_or_partial_earnings_never_passes_clean_gate():
    matrix = build_v4a_data_readiness(
        capital_summary=_capital(),
        financing_summary=_financing(),
        pit_summary=_pit(earnings_direction_readiness_state="PARTIAL_COVERAGE"),
    )
    assert matrix["earnings_pit"] == "PARTIAL_COVERAGE"
    assert matrix["clean_forward_external_evidence"] == "DATA_INSUFFICIENT"


def test_major_negative_coverage_incomplete_fails_closed():
    matrix = build_v4a_data_readiness(
        capital_summary=_capital(),
        financing_summary=_financing(),
        pit_summary=_pit(major_negative_event_exclusion_complete=False),
    )
    assert matrix["major_negative_exclusion"] == "DATA_INSUFFICIENT"
    assert matrix["clean_forward_external_evidence"] == "DATA_INSUFFICIENT"


def test_revision_or_replay_failure_fails_clean_gate():
    pit = _pit()
    pit["pit_audit"] = dict(pit["pit_audit"])
    pit["pit_audit"]["later_revision_does_not_rewrite_prior_rows"] = False
    matrix = build_v4a_data_readiness(
        capital_summary=_capital(),
        financing_summary=_financing(),
        pit_summary=pit,
    )
    assert matrix["fundamental_pit"] == "QUALIFIED_INPUT"
    assert matrix["clean_forward_external_evidence"] == "DATA_INSUFFICIENT"


def test_financing_is_assessed_separately_even_though_not_in_clean_context_composite():
    matrix = build_v4a_data_readiness(
        capital_summary=_capital(),
        financing_summary=_financing(bilateral_coverage=0.5),
        pit_summary=_pit(),
    )
    assert matrix["financing"] == "PARTIAL_COVERAGE"
    # Financing remains RESEARCH_INPUT and is not a Capital Regime composite input.
    assert matrix["clean_forward_external_evidence"] == "QUALIFIED_INPUT"
