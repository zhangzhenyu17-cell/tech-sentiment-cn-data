from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "run_931152_oos_inputs_resumable.py"
SPEC = importlib.util.spec_from_file_location("run_931152_oos_inputs_resumable", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _valid_page(symbol: str) -> str:
    return f"""
    <html><body><h1>{symbol} 相关资料</h1><h3>所属指数</h3>
    <table>
      <tr><th>指数名称</th><th>指数代码</th><th>进入日期</th><th>退出日期</th></tr>
      <tr><td>CS创新药</td><td>931152</td><td>2019-04-22</td><td></td></tr>
    </table></body></html>
    """


def test_successful_sina_page_is_reused_from_checkpoint(tmp_path, monkeypatch) -> None:
    symbol = "600276"
    html = _valid_page(symbol)
    calls: list[str] = []
    sleeps: list[float] = []

    def fetch_related_page(value: str, **kwargs):
        calls.append(value)
        return html, MODULE.sina.related_url(value), "a" * 64

    monkeypatch.setattr(MODULE.sina, "fetch_related_page", fetch_related_page)
    monkeypatch.setattr(MODULE.time, "sleep", sleeps.append)
    fetcher = MODULE._checkpointed_fetcher(tmp_path)

    intervals, returned_html = fetcher(symbol, timeout=10, index_code="931152")
    assert len(intervals) == 1
    assert returned_html == html
    assert calls == [symbol]
    assert sleeps == [MODULE.NETWORK_SPACING_SECONDS]

    meta_path = tmp_path / f"sina_{symbol}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["response_sha256"] == "a" * 64
    assert meta["source_url"] == MODULE.sina.related_url(symbol)

    monkeypatch.setattr(
        MODULE.sina,
        "fetch_related_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used")),
    )
    sleeps.clear()
    intervals2, returned_html2 = fetcher(symbol, timeout=10, index_code="931152")
    assert len(intervals2) == 1
    assert returned_html2 == html
    assert sleeps == []


def test_http_456_gets_provider_cooldown_before_retry(tmp_path, monkeypatch) -> None:
    symbol = "600196"
    html = _valid_page(symbol)
    attempts = 0
    sleeps: list[float] = []

    def fetch_related_page(value: str, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("browser=HTTPError: HTTP Error 456")
        return html, MODULE.sina.related_url(value), "b" * 64

    monkeypatch.setattr(MODULE.sina, "fetch_related_page", fetch_related_page)
    monkeypatch.setattr(MODULE.time, "sleep", sleeps.append)
    fetcher = MODULE._checkpointed_fetcher(tmp_path)

    intervals, _ = fetcher(symbol, index_code="931152")
    assert len(intervals) == 1
    assert attempts == 2
    assert sleeps == [MODULE.NETWORK_SPACING_SECONDS, MODULE.RATE_LIMIT_COOLDOWN_SECONDS]


def test_corrupted_checkpoint_is_not_silently_accepted(tmp_path, monkeypatch) -> None:
    symbol = "600276"
    html_path, meta_path = MODULE._checkpoint_paths(tmp_path, symbol)
    html_path.write_text(_valid_page(symbol), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "schema_version": MODULE.CHECKPOINT_SCHEMA,
                "symbol": symbol,
                "index_code": "931152",
                "source_url": MODULE.sina.related_url(symbol),
                "response_sha256": "c" * 64,
                "checkpoint_text_sha256": "wrong",
            }
        ),
        encoding="utf-8",
    )

    calls = 0

    def fetch_related_page(value: str, **kwargs):
        nonlocal calls
        calls += 1
        html = _valid_page(value)
        return html, MODULE.sina.related_url(value), "d" * 64

    monkeypatch.setattr(MODULE.sina, "fetch_related_page", fetch_related_page)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    intervals, _ = MODULE._checkpointed_fetcher(tmp_path)(symbol, index_code="931152")
    assert len(intervals) == 1
    assert calls == 1
