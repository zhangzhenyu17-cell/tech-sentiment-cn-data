import runpy

import pandas as pd

from tech_sentiment.immutable_checkpoint import ImmutableCheckpointStore
from tech_sentiment.official_pit_archives import SSE_SOURCE_ID
from tech_sentiment.pit_public_materialization import PitMaterializationResult


issuer_script = runpy.run_path("scripts/materialize_pit_evidence.py")


def _result(*, status: str, with_error: bool) -> PitMaterializationResult:
    coverage = pd.DataFrame(
        [
            {
                "source_identity": SSE_SOURCE_ID,
                "entity_id": "600276.SH",
                "coverage_start": "2022-01-04",
                "coverage_end": "2026-09-17",
                "query_status": status,
                "records": 0,
            }
        ]
    )
    errors = (
        pd.DataFrame(
            [
                {
                    "source_identity": SSE_SOURCE_ID,
                    "entity_id": "600276.SH",
                    "error": "HTTPError: transient fixture",
                }
            ]
        )
        if with_error
        else pd.DataFrame(columns=["source_identity", "entity_id", "error"])
    )
    return PitMaterializationResult(
        records=pd.DataFrame(),
        coverage=coverage,
        errors=errors,
        summary={"tail_records_beyond_asof_not_materialized": 0},
    )


def test_failed_issuer_query_is_not_checkpointed_and_success_is_resumable(tmp_path):
    store = ImmutableCheckpointStore(tmp_path)
    kwargs = dict(
        source=SSE_SOURCE_ID,
        symbols=["600276"],
        start_date="2022-01-04",
        end_date="2026-09-17",
        source_commit="fixture-sha",
        calendar_identity="fixture-calendar",
        store=store,
    )

    failed_calls = 0

    def fail_once(symbol: str) -> PitMaterializationResult:
        nonlocal failed_calls
        failed_calls += 1
        return _result(status="FAILED", with_error=True)

    _, failed_coverage, failed_errors, failed_summary = issuer_script["_run_source"](
        **kwargs,
        materialize_one=fail_once,
    )
    assert failed_calls == 1
    assert failed_summary["readiness_state"] == "DATA_INSUFFICIENT"
    assert failed_summary["failed_symbol_queries"] == 1
    assert len(failed_errors) == 1
    assert failed_coverage.iloc[0]["query_status"] == "FAILED"

    identity = issuer_script["_checkpoint_identity"](
        source=SSE_SOURCE_ID,
        symbol="600276",
        start_date="2022-01-04",
        end_date="2026-09-17",
        source_commit="fixture-sha",
        calendar_identity="fixture-calendar",
    )
    assert store.load(identity) is None

    success_calls = 0

    def succeed(symbol: str) -> PitMaterializationResult:
        nonlocal success_calls
        success_calls += 1
        return _result(status="COMPLETE_WINDOW", with_error=False)

    _, success_coverage, success_errors, success_summary = issuer_script["_run_source"](
        **kwargs,
        materialize_one=succeed,
    )
    assert success_calls == 1
    assert success_summary["readiness_state"] == "QUALIFIED_INPUT"
    assert success_summary["failed_symbol_queries"] == 0
    assert success_errors.empty
    assert success_coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"
    assert store.load(identity) is not None

    def must_not_execute(symbol: str) -> PitMaterializationResult:
        raise AssertionError("successful issuer checkpoint should resume without refetch")

    _, resumed_coverage, resumed_errors, resumed_summary = issuer_script["_run_source"](
        **kwargs,
        materialize_one=must_not_execute,
    )
    assert resumed_summary["resumed_symbol_queries"] == 1
    assert resumed_summary["executed_symbol_queries"] == 0
    assert resumed_summary["readiness_state"] == "QUALIFIED_INPUT"
    assert resumed_errors.empty
    assert resumed_coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"


def test_issuer_checkpoint_schema_declares_success_only_contract():
    assert (
        issuer_script["ISSUER_PIT_CHECKPOINT_VERSION"]
        == "issuer-pit-exact-identity-v3-success-only"
    )
