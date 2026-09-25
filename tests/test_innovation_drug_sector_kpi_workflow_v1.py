from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/innovation-drug-sector-kpi-raw-v1.yml"


def test_sector_kpi_workflow_is_manual_only_and_exact_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: Innovation Drug Sector KPI Raw V1\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("\n  schedule:", "\n  push:", "\n  pull_request:", "\n  workflow_run:"):
        assert forbidden not in text
    assert 'SYMBOL: "600276"' in text
    assert 'START_DATE: "2020-01-01"' in text
    assert "materialize_innovation_drug_sector_kpi_raw_v1.py" in text
    assert "innovation-drug-sector-kpi-raw-v1-600276" in text


def test_sector_kpi_workflow_has_no_private_or_predictive_payload() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "private_threshold",
        "forward_return",
        "model_weight",
        "trading_signal",
        "position_size",
        "tech-sentiment-cn.git",
    ):
        assert forbidden not in text.lower()


def test_successor_calendar_extension_only_appends_after_shared_max(tmp_path, monkeypatch) -> None:
    import importlib.util
    import sys
    from types import SimpleNamespace
    import pandas as pd

    script = ROOT / "scripts/materialize_innovation_drug_sector_kpi_raw_v1.py"
    spec = importlib.util.spec_from_file_location("id_sector_kpi_calendar_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": ["2026-09-23", "2026-09-24", "2026-09-25"]}).to_csv(calendar, index=False)
    fake_ak = SimpleNamespace(
        tool_trade_date_hist_sina=lambda: pd.DataFrame(
            {"trade_date": ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]}
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    dates, metadata = module._trade_dates(calendar, evidence_cutoff="2026-09-25")
    assert [str(pd.Timestamp(value).date()) for value in dates[:3]] == [
        "2026-09-23", "2026-09-24", "2026-09-25"
    ]
    assert [str(pd.Timestamp(value).date()) for value in dates[3:]] == [
        "2026-09-28", "2026-09-29"
    ]
    assert metadata["shared_calendar_max_date"] == "2026-09-25"
    assert metadata["effective_calendar_max_date"] == "2026-09-29"
    assert metadata["successor_extension_source"] == (
        "AKSHARE_TOOL_TRADE_DATE_HIST_SINA_AFTER_SHARED_MAX_ONLY"
    )
    assert metadata["successor_extension_used_only_for_availability_alignment"] is True


def test_successor_calendar_fails_closed_without_post_cutoff_session(tmp_path, monkeypatch) -> None:
    import importlib.util
    import sys
    from types import SimpleNamespace
    import pandas as pd
    import pytest

    script = ROOT / "scripts/materialize_innovation_drug_sector_kpi_raw_v1.py"
    spec = importlib.util.spec_from_file_location("id_sector_kpi_calendar_fail_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": ["2026-09-24", "2026-09-25"]}).to_csv(calendar, index=False)
    fake_ak = SimpleNamespace(
        tool_trade_date_hist_sina=lambda: pd.DataFrame(
            {"trade_date": ["2026-09-24", "2026-09-25"]}
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    with pytest.raises(ValueError, match="lacks a known successor session"):
        module._trade_dates(calendar, evidence_cutoff="2026-09-25")
