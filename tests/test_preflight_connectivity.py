import runpy

import pandas as pd


issuer_probe = runpy.run_path("scripts/check_issuer_archive_connectivity.py")
policy_probe = runpy.run_path("scripts/check_csrc_policy_connectivity.py")
cninfo_probe = runpy.run_path("scripts/check_cninfo_connectivity.py")


def test_issuer_preflight_retries_transient_transport_reset(monkeypatch):
    calls = 0

    def fetcher(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(104, "reset")
        return pd.DataFrame(
            [
                {
                    "symbol": "000538",
                    "title": "2023年年度报告",
                    "publication_time": "2024-03-30 00:00:00",
                    "document_id": "doc-1",
                    "source_url": "https://disc.static.szse.cn/download/doc-1.pdf",
                }
            ]
        )

    monkeypatch.setitem(issuer_probe, "fetch_szse_announcements", fetcher)
    issuer_probe["_run_probe"].__globals__["fetch_szse_announcements"] = fetcher
    result = issuer_probe["_run_probe"](
        {
            "market": "SZSE",
            "symbol": "000538",
            "start_date": "2024-03-29",
            "end_date": "2024-03-31",
            "expected_title_token": "2023年年度报告",
        }
    )

    assert calls == 2
    assert result["protocol_ok"] is True
    assert result["document_id_present"] is True
    assert result["official_https_url_present"] is True

def test_policy_preflight_retries_transient_timeout_with_short_bound(monkeypatch):
    calls = 0

    def fetcher(url, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 10.0
        if calls == 1:
            raise TimeoutError("timed out")
        return {"code": 200}

    monkeypatch.setitem(policy_probe, "_fetch_json", fetcher)
    policy_probe["_probe_fetch_json"].__globals__["_fetch_json"] = fetcher

    result = policy_probe["_probe_fetch_json"]("https://www.csrc.gov.cn/getLocalList?channelCode=c101953")

    assert calls == 2
    assert result == {"code": 200}




def _cninfo_row(title: str, publication: str, document_id: str) -> dict[str, str]:
    return {
        "公告标题": title,
        "公告时间": publication,
        "公告链接": (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"announcementId={document_id}&orgId=gssh0600519"
        ),
        "公告附件链接": (
            "https://static.cninfo.com.cn/finalpage/2023-03-31/"
            f"{document_id}.PDF"
        ),
    }


def test_cninfo_preflight_selects_chinese_numeric_report_over_english_translation():
    frame = pd.DataFrame(
        [
            _cninfo_row(
                "贵州茅台2022年年度报告（英文版）",
                "2023-03-31 00:00:00",
                "1216281758",
            ),
            _cninfo_row(
                "贵州茅台2022年年度报告",
                "2023-03-31 00:00:00",
                "1216281757",
            ),
        ]
    )

    selected, raw_count, eligible_count = cninfo_probe[
        "_select_numeric_report_candidate"
    ](
        frame,
        expected_title_token="2022年年度报告",
    )

    assert raw_count == 2
    assert eligible_count == 1
    assert selected["公告标题"] == "贵州茅台2022年年度报告"
    assert "1216281757" in selected["公告链接"]


def test_cninfo_preflight_prefers_latest_eligible_revision_not_source_row_order():
    frame = pd.DataFrame(
        [
            _cninfo_row(
                "贵州茅台2022年年度报告",
                "2023-03-31 00:00:00",
                "1216281757",
            ),
            _cninfo_row(
                "贵州茅台2022年年度报告（修订版）",
                "2023-04-10 18:00:00",
                "1217000000",
            ),
            _cninfo_row(
                "贵州茅台2022年年度报告（英文版）",
                "2023-04-11 00:00:00",
                "1217000001",
            ),
        ]
    )

    selected, raw_count, eligible_count = cninfo_probe[
        "_select_numeric_report_candidate"
    ](
        frame.iloc[::-1].reset_index(drop=True),
        expected_title_token="2022年年度报告",
    )

    assert raw_count == 3
    assert eligible_count == 2
    assert selected["公告标题"] == "贵州茅台2022年年度报告（修订版）"


def test_cninfo_preflight_fails_closed_on_ambiguous_latest_eligible_reports():
    frame = pd.DataFrame(
        [
            _cninfo_row(
                "贵州茅台2022年年度报告",
                "2023-03-31 00:00:00",
                "1216281757",
            ),
            _cninfo_row(
                "贵州茅台2022年年度报告（修订版）",
                "2023-03-31 00:00:00",
                "1216281759",
            ),
        ]
    )

    try:
        cninfo_probe["_select_numeric_report_candidate"](
            frame,
            expected_title_token="2022年年度报告",
        )
    except ValueError as exc:
        assert "ambiguous latest eligible financial reports" in str(exc)
    else:
        raise AssertionError("ambiguous latest CNINFO reports must fail closed")
