from __future__ import annotations

import pandas as pd

from tech_sentiment.bse50_official import (
    OfficialSnapshot,
    discover_attachment_links,
    discover_notice_links,
    extract_effective_date,
    snapshots_to_membership,
)


def _codes(prefix: int) -> tuple[str, ...]:
    return tuple(f"83{prefix:02d}{i:02d}" for i in range(50))


def test_discover_links_and_effective_date():
    html = """
    <html><body>
      <a href="/bse_indices_news/200027851.html">北证50样本调整</a>
      <a href="/uploads/6/file/public/202602/sample.xlsx">附件</a>
      根据指数编制方案，于2026年3月16日正式生效。
    </body></html>
    """
    assert discover_notice_links(html, base_url="https://www.bse.cn/bse_index/notice.html") == [
        "https://www.bse.cn/bse_indices_news/200027851.html"
    ]
    assert discover_attachment_links(html, base_url="https://www.bse.cn/bse_indices_news/200027851.html") == [
        "https://www.bse.cn/uploads/6/file/public/202602/sample.xlsx"
    ]
    assert extract_effective_date(html) == pd.Timestamp("2026-03-16")


def test_snapshots_build_contiguous_point_in_time_membership():
    s1 = OfficialSnapshot(
        effective_date=pd.Timestamp("2022-11-21"),
        symbols=_codes(10),
        notice_url="https://www.bse.cn/important_news/200013611.html",
        attachment_url="https://www.bse.cn/a.xlsx",
    )
    s2 = OfficialSnapshot(
        effective_date=pd.Timestamp("2022-12-12"),
        symbols=_codes(20),
        notice_url="https://www.bse.cn/bse_indices_news/200013968.html",
        attachment_url="https://www.bse.cn/b.xlsx",
    )
    out = snapshots_to_membership(
        [s1, s2],
        history_start="2022-11-21",
        history_end="2022-12-31",
    )
    assert len(out) == 100
    assert set(out["board"]) == {"beijing"}
    assert set(out["limit_pct"]) == {30.0}
    first = out[out["effective_start"].eq(pd.Timestamp("2022-11-21"))]
    second = out[out["effective_start"].eq(pd.Timestamp("2022-12-12"))]
    assert first["effective_end"].iloc[0] == pd.Timestamp("2022-12-11")
    assert second["effective_end"].iloc[0] == pd.Timestamp("2022-12-31")
