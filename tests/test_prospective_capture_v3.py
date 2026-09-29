from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from tech_sentiment.capital_input_data import ExchangeTurnoverFetchResult, EtfShareFetchResult
from tech_sentiment.prospective_capture_timing_v3 import validate_capture_pair_v3
from tech_sentiment.prospective_source_observation_v3 import (
    capture_capital_source_observations_v3,
    package_complete_source_observation_v3,
)
from tech_sentiment.v4c03_szse_etf_shares import SzseEtfShareFetchResult


class _CalendarClient:
    def tool_trade_date_hist_sina(self):
        return pd.DataFrame(
            {"trade_date": ["2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30"]}
        )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def test_v3_allows_after_0530_but_marks_cutoff_eligibility() -> None:
    before_cutoff = validate_capture_pair_v3(
        market_session_date="2026-09-28",
        decision_date="2026-09-29",
        observed_at=_dt("2026-09-29T08:30:00"),
        client=_CalendarClient(),
    )
    assert before_cutoff["capture_allowed"] is True
    assert before_cutoff["shadow_decision_eligible_by_time"] is True
    assert before_cutoff["timing_class"] == "LATE_PREOPEN_BEFORE_SHADOW_DECISION_CUTOFF"

    after_cutoff = validate_capture_pair_v3(
        market_session_date="2026-09-28",
        decision_date="2026-09-29",
        observed_at=_dt("2026-09-29T09:45:00"),
        client=_CalendarClient(),
    )
    assert after_cutoff["capture_allowed"] is True
    assert after_cutoff["shadow_decision_eligible_by_time"] is False
    assert after_cutoff["timing_class"] == "POST_EXECUTION_LATE_RECOVERY"


def test_v3_rejects_before_market_close() -> None:
    try:
        validate_capture_pair_v3(
            market_session_date="2026-09-28",
            decision_date="2026-09-29",
            observed_at=_dt("2026-09-28T14:59:59"),
            client=_CalendarClient(),
        )
    except ValueError as exc:
        assert "before market-session close" in str(exc)
    else:
        raise AssertionError("expected before-close rejection")


def test_source_observations_persist_successes_independently(tmp_path: Path) -> None:
    session = pd.Timestamp("2026-09-28")
    sse = EtfShareFetchResult(
        data=pd.DataFrame(
            [{
                "date": session,
                "fund_code": "588000",
                "fund_shares": 1.0,
                "unit": "share",
                "source_identity": "SSE",
                "source_url": "https://query.sse.com.cn/",
                "provider_interface": "commonQuery",
                "evidence_available_date": session,
            }]
        ),
        errors=pd.DataFrame(columns=["date", "error"]),
    )
    szse = SzseEtfShareFetchResult(
        data=pd.DataFrame(
            [{
                "date": session,
                "fund_code": "159915",
                "fund_shares": 2.0,
                "unit": "share",
                "source_identity": "SZSE",
                "provider": "SZSE",
                "evidence_available_date": session,
            }]
        ),
        errors=pd.DataFrame(columns=["chunk_start", "chunk_end", "error"]),
    )
    turnover = ExchangeTurnoverFetchResult(
        sse=pd.DataFrame([{"date": session, "amount": 3.0}]),
        szse=pd.DataFrame(columns=["date", "amount"]),
        combined=pd.DataFrame(columns=["date", "amount"]),
        errors=pd.DataFrame(
            [{"date": "2026-09-28", "exchange": "SZSE", "error": "temporary"}]
        ),
    )

    observations = capture_capital_source_observations_v3(
        market_session_date="2026-09-28",
        decision_date="2026-09-29",
        source_commit="abc",
        output_root=tmp_path / "obs",
        observed_at=_dt("2026-09-29T09:45:00"),
        client=_CalendarClient(),
        transport_origin="GITHUB_HOSTED",
        runner_name="test-runner",
        sse_etf_fetcher=lambda **_: sse,
        szse_etf_fetcher=lambda **_: szse,
        turnover_fetcher=lambda **_: turnover,
        clock=lambda: _dt("2026-09-29T09:45:01"),
    )

    assert observations["SSE_588000"].state == "COMPLETE"
    assert observations["SZSE_159915"].state == "COMPLETE"
    assert observations["SSE_TURNOVER"].state == "COMPLETE"
    assert observations["SZSE_TURNOVER"].state == "SOURCE_FAILURE_OR_INCOMPLETE"
    assert observations["SSE_588000"].receipt["shadow_decision_eligible"] is False
    assert observations["SSE_588000"].receipt["formal_evidence_handoff"] is False

    manifest = package_complete_source_observation_v3(
        observations["SSE_588000"], output_dir=tmp_path / "packages"
    )
    assert manifest["release_tag"].endswith("2026-09-28-SSE_588000")
    assert manifest["shadow_decision_eligible"] is False
    assert manifest["formal_evidence_handoff"] is False
    assert (tmp_path / "packages" / f"{manifest['release_tag']}.tar.gz").is_file()


def test_source_first_observed_uses_fetch_completion_not_attempt_start(tmp_path: Path) -> None:
    session = pd.Timestamp("2026-09-28")
    sse = EtfShareFetchResult(
        data=pd.DataFrame(
            [{
                "date": session,
                "fund_code": "588000",
                "fund_shares": 1.0,
                "unit": "share",
                "source_identity": "SSE",
                "source_url": "https://query.sse.com.cn/",
                "provider_interface": "commonQuery",
                "evidence_available_date": session,
            }]
        ),
        errors=pd.DataFrame(columns=["date", "error"]),
    )
    observations = capture_capital_source_observations_v3(
        market_session_date="2026-09-28",
        decision_date="2026-09-29",
        source_commit="abc",
        output_root=tmp_path / "obs",
        observed_at=_dt("2026-09-29T08:44:50"),
        client=_CalendarClient(),
        transport_origin="GITHUB_HOSTED",
        runner_name="test-runner",
        sse_etf_fetcher=lambda **_: sse,
        source_keys=("SSE_588000",),
        clock=lambda: _dt("2026-09-29T08:45:01"),
    )
    receipt = observations["SSE_588000"].receipt
    assert receipt["observation_attempt_at_asia_shanghai"].endswith("08:44:50+08:00")
    assert receipt["source_fetch_completed_at_asia_shanghai"].endswith("08:45:01+08:00")
    assert receipt["first_observed_at_asia_shanghai"].endswith("08:45:01+08:00")
    assert receipt["observation_timestamp_semantics"] == "SOURCE_FETCH_COMPLETION_TIME"
    assert receipt["shadow_decision_eligible"] is False

def test_v3_rejects_unregistered_transport_origin(tmp_path: Path) -> None:
    try:
        capture_capital_source_observations_v3(
            market_session_date="2026-09-28",
            decision_date="2026-09-29",
            source_commit="abc",
            output_root=tmp_path / "obs",
            observed_at=_dt("2026-09-29T08:30:00"),
            client=_CalendarClient(),
            transport_origin="UNREGISTERED_TRANSPORT",
            source_keys=("SSE_588000",),
        )
    except ValueError as exc:
        assert "unsupported V3 transport origin" in str(exc)
    else:
        raise AssertionError("expected transport-origin rejection")


def test_v3_sidecars_heal_from_exact_archive(tmp_path: Path) -> None:
    import json
    from scripts.heal_prospective_source_observation_v3 import heal_sidecars
    from tech_sentiment.prospective_source_observation_v3 import SourceObservation

    root = tmp_path / "SSE_588000"
    root.mkdir()
    receipt = {
        "schema_version": "prospective-source-observation-v3",
        "source_key": "SSE_588000",
        "state": "COMPLETE",
        "market_session_date": "2026-09-28",
        "decision_date": "2026-09-29",
        "first_observed_at_asia_shanghai": "2026-09-29T08:30:00+08:00",
        "shadow_decision_eligible": True,
    }
    (root / "SOURCE_OBSERVATION_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "data.csv").write_text("date,value\n2026-09-28,1\n", encoding="utf-8")
    observation = SourceObservation("SSE_588000", "COMPLETE", root, receipt)
    original = package_complete_source_observation_v3(
        observation, output_dir=tmp_path / "original"
    )
    archive = tmp_path / "original" / f"{original['release_tag']}.tar.gz"
    healed = heal_sidecars(archive, tmp_path / "healed")
    assert healed == original
