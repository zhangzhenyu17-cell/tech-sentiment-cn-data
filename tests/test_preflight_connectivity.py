import pandas as pd

import scripts.check_issuer_archive_connectivity as issuer_probe


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

    monkeypatch.setattr(issuer_probe, "fetch_szse_announcements", fetcher)
    result = issuer_probe._run_probe(
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
