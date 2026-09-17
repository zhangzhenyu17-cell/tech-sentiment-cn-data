from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
import re
import time
from typing import Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SINA_RELATED_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/"
    "vCI_CorpXiangGuan/stockid/{symbol}.phtml"
)
CSI_931152 = "931152"


@dataclass(frozen=True)
class IndexMembershipInterval:
    symbol: str
    index_code: str
    index_name: str
    start_date: str
    end_date: str
    source_url: str
    response_sha256: str

    def active_on(self, value: str | date) -> bool:
        """Return membership using half-open [start, end) semantics.

        Sina's second date is treated as the effective removal date: a stock
        that shows an end date equal to a rebalance effective date is not part
        of the post-rebalance set on that date.
        """

        target = value if isinstance(value, date) else date.fromisoformat(str(value))
        start = date.fromisoformat(self.start_date)
        if target < start:
            return False
        if not self.end_date:
            return True
        end = date.fromisoformat(self.end_date)
        return target < end


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(_clean_text(" ".join(self.cell_parts)))
            self.in_cell = False
            self.cell_parts = []
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(list(self.row))
            self.in_row = False
            self.row = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def related_url(symbol: str) -> str:
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("symbol must be six digits")
    return SINA_RELATED_URL.format(symbol=symbol)


def parse_membership_intervals(
    html: str,
    *,
    symbol: str,
    source_url: str,
    response_sha256: str,
    index_code: str = CSI_931152,
) -> list[IndexMembershipInterval]:
    """Parse exact index-code rows from Sina's related-index table.

    Matching is deliberately cell-exact. Codes such as ``931152USD200`` must
    never be admitted merely because they contain the target code as a prefix.
    Dates are parsed only from cells after the exact code cell.
    """

    parser = _TableParser()
    parser.feed(html)
    out: list[IndexMembershipInterval] = []
    target_code = _clean_text(index_code)
    for cells in parser.rows:
        cleaned = [_clean_text(cell) for cell in cells]
        code_positions = [idx for idx, cell in enumerate(cleaned) if cell == target_code]
        if not code_positions:
            continue
        code_pos = code_positions[0]
        trailing = " | ".join(cleaned[code_pos + 1 :])
        dates = re.findall(r"(?:19|20)\d{2}-\d{2}-\d{2}", trailing)
        if not dates:
            continue
        start = dates[0]
        end = dates[1] if len(dates) > 1 else ""
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end) if end else None
        except ValueError:
            continue
        if end_date is not None and end_date <= start_date:
            continue
        name = cleaned[code_pos - 1] if code_pos > 0 else ""
        out.append(
            IndexMembershipInterval(
                symbol=symbol,
                index_code=target_code,
                index_name=name,
                start_date=start,
                end_date=end,
                source_url=source_url,
                response_sha256=response_sha256,
            )
        )
    dedup = {
        (item.start_date, item.end_date, item.index_code): item
        for item in out
    }
    return sorted(dedup.values(), key=lambda item: (item.start_date, item.end_date))


def summarize_interval_coverage(
    intervals_by_symbol: Mapping[str, list[IndexMembershipInterval]],
    *,
    candidate_symbols: Iterable[str],
) -> dict[str, int]:
    """Summarize interval evidence without counting empty fetches as evidence."""

    candidates = set(candidate_symbols)
    with_intervals = {
        symbol
        for symbol in candidates
        if intervals_by_symbol.get(symbol)
    }
    interval_rows = sum(
        len(intervals_by_symbol.get(symbol, ()))
        for symbol in candidates
    )
    return {
        "candidate_symbols": len(candidates),
        "symbols_with_intervals": len(with_intervals),
        "symbols_without_intervals": len(candidates - with_intervals),
        "interval_rows": interval_rows,
    }


def _decode_related_page(raw: bytes, charset: str | None) -> str:
    if not raw:
        raise ValueError("Sina returned an empty related-info page")
    for encoding in (charset, "gb18030", "utf-8"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _validate_related_page(text: str, *, symbol: str) -> None:
    """Reject WAF/challenge/error bodies before they can look like no-membership evidence."""

    compact = _clean_text(re.sub(r"<[^>]+>", " ", text))
    markers = ("所属指数", "指数代码")
    if not all(marker in compact for marker in markers):
        raise ValueError(f"Sina related-info page failed semantic validation for {symbol}")
    if symbol not in compact:
        raise ValueError(f"Sina related-info page does not identify requested symbol {symbol}")


def _fetch_related_page_browser(url: str, *, timeout: int) -> tuple[bytes, str | None]:
    """Use a browser-fingerprint transport against the same public Sina URL.

    This is transport fallback only. The response is subjected to the exact same
    semantic validation and parsing as the urllib path.
    """

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - installed by the data extra in workflow
        raise RuntimeError("curl_cffi is required for Sina browser transport fallback") from exc

    response = curl_requests.get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer": "https://finance.sina.com.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
        impersonate="chrome",
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    raw = bytes(response.content)
    charset = getattr(response, "encoding", None)
    return raw, charset


def fetch_related_page(
    symbol: str,
    *,
    timeout: int = 20,
    retries: int = 4,
    retry_backoff_seconds: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    browser_fetcher: Callable[..., tuple[bytes, str | None]] | None = None,
) -> tuple[str, str, str]:
    """Fetch one Sina related-info page with bounded transport retries and fallback.

    The evidence URL and parser are unchanged. urllib is attempted first. If that
    transport is blocked or exhausted (including WAF-style HTTP failures), one
    browser-fingerprint request is attempted against the same URL. Both paths must
    return a semantically valid Sina related-info page; challenge/error bodies and
    empty responses remain fail-closed.
    """

    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if timeout <= 0:
        raise ValueError("timeout must be > 0")

    url = related_url(symbol)
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
            ),
            "Connection": "close",
        },
        method="GET",
    )

    last_error: Exception | None = None
    retry_offset = (int(symbol[-2:]) % 7) * 0.05
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public provider
                raw = response.read()
                charset = response.headers.get_content_charset()
            text = _decode_related_page(raw, charset)
            _validate_related_page(text, symbol=symbol)
            return text, url, sha256(raw).hexdigest()
        except HTTPError as exc:
            last_error = exc
            # A 4xx can be a TLS/browser-fingerprint WAF decision on hosted runners.
            # Do not loop client errors through urllib; proceed to the separately
            # validated browser transport below.
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc

        if attempt < retries:
            sleep_fn(retry_backoff_seconds * (attempt + 1) + retry_offset)

    fallback = browser_fetcher or _fetch_related_page_browser
    try:
        raw, charset = fallback(url, timeout=timeout)
        text = _decode_related_page(raw, charset)
        _validate_related_page(text, symbol=symbol)
        return text, url, sha256(raw).hexdigest()
    except Exception as exc:
        if last_error is None:
            last_error = exc
        raise RuntimeError(
            f"Sina related-info fetch failed for {symbol}; urllib={type(last_error).__name__}; "
            f"browser={type(exc).__name__}: {exc}"
        ) from exc


def fetch_membership_intervals(
    symbol: str,
    *,
    timeout: int = 20,
    index_code: str = CSI_931152,
) -> tuple[list[IndexMembershipInterval], str]:
    html, url, digest = fetch_related_page(symbol, timeout=timeout)
    intervals = parse_membership_intervals(
        html,
        symbol=symbol,
        source_url=url,
        response_sha256=digest,
        index_code=index_code,
    )
    return intervals, html
