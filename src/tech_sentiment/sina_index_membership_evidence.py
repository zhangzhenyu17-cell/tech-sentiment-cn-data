from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
import re
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
    parser = _TableParser()
    parser.feed(html)
    out: list[IndexMembershipInterval] = []
    for cells in parser.rows:
        joined = " | ".join(cells)
        if index_code not in joined:
            continue
        dates = re.findall(r"(?:19|20)\d{2}-\d{2}-\d{2}", joined)
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
        name = ""
        for cell in cells:
            if "创新药" in cell:
                name = cell
                break
        out.append(
            IndexMembershipInterval(
                symbol=symbol,
                index_code=index_code,
                index_name=name,
                start_date=start,
                end_date=end,
                source_url=source_url,
                response_sha256=response_sha256,
            )
        )
    # A duplicate rendering of the same row must not create duplicate evidence.
    dedup = {
        (item.start_date, item.end_date, item.index_code): item
        for item in out
    }
    return sorted(dedup.values(), key=lambda item: (item.start_date, item.end_date))


def fetch_related_page(symbol: str, *, timeout: int = 20) -> tuple[str, str, str]:
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
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public provider
        raw = response.read()
        charset = response.headers.get_content_charset()
    if not raw:
        raise ValueError("Sina returned an empty related-info page")
    candidates = [charset, "gb18030", "utf-8"]
    text = ""
    for encoding in candidates:
        if not encoding:
            continue
        try:
            text = raw.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if not text:
        text = raw.decode("utf-8", errors="replace")
    return text, url, sha256(raw).hexdigest()


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
