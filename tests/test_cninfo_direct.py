from tech_sentiment.cninfo_direct import CNINFO_STATIC_ORIGIN, _attachment_url


def test_cninfo_attachment_url_preserves_exact_official_version_identity():
    relative = "finalpage/2026-04-30/1234567890.PDF"
    assert _attachment_url(relative) == f"{CNINFO_STATIC_ORIGIN}/{relative}"
    absolute = "https://static.cninfo.com.cn/finalpage/2026-04-30/abc.PDF"
    assert _attachment_url(absolute) == absolute
    assert _attachment_url("") == ""
