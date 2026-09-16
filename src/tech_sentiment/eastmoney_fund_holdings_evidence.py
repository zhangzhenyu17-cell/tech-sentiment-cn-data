from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EASTMONEY_FUND_HOLDINGS_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
EASTMONEY_FUND_PAGE = "https://fundf10.eastmoney.com/ccmx_{code}.html"
TRACKING_ETF_931152 = "159992"


@dataclass(frozen=True)
class FundHoldingBatch:
    fund_code: str
    report_date: str
    quarter: int
    heading: str
    symbols: tuple[str, ...]
    source_url: str
    response_sha256: str
    evidence_kind: str
    full_report_candidate_set: bool


class _FundBoxParser(HTMLParser):
    """Parse report boxes returned inside EastMoney's ``content`` field."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.box_depth = 0
        self.in_h4 = False
        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.heading_parts: list[str] = []
        self.cell_parts: list[str] = []
        self.current_row: list[str] = []
        self.current_rows: list[list[str]] = []
        self.boxes: list[tuple[str, list[list[str]]]] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if key == "class" and value:
                return set(value.split())
        return set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            classes = self._classes(attrs)
            if self.box_depth == 0 and "box" in classes:
                self.box_depth = 1
                self.heading_parts = []
                self.current_rows = []
                return
            if self.box_depth:
                self.box_depth += 1

        if not self.box_depth:
            return
        if tag == "h4":
            self.in_h4 = True
        elif tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_tr = True
            self.current_row = []
        elif tag == "td" and self.in_tr:
            self.in_td = True
            self.cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self.box_depth:
            return
        if tag == "td" and self.in_td:
            self.current_row.append(_clean_text(" ".join(self.cell_parts)))
            self.in_td = False
            self.cell_parts = []
        elif tag == "tr" and self.in_tr:
            if self.current_row:
                self.current_rows.append(self.current_row)
            self.in_tr = False
            self.current_row = []
        elif tag == "table":
            self.in_table = False
        elif tag == "h4":
            self.in_h4 = False
        elif tag == "div":
            self.box_depth -= 1
            if self.box_depth == 0:
                heading = _clean_text(" ".join(self.heading_parts))
                self.boxes.append((heading, list(self.current_rows)))
                self.heading_parts = []
                self.current_rows = []

    def handle_data(self, data: str) -> None:
        if not self.box_depth:
            return
        if self.in_h4:
            self.heading_parts.append(data)
        if self.in_td:
            self.cell_parts.append(data)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_js_quoted_field(text: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*([\"'])", text)
    if not match:
        raise ValueError(f"EastMoney response missing quoted field {key!r}")
    quote = match.group(1)
    start = match.end()
    escaped = False
    chars: list[str] = []
    for idx in range(start, len(text)):
        char = text[idx]
        if escaped:
            chars.extend(("\\", char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            raw = "".join(chars)
            if quote == '"':
                return json.loads('"' + raw + '"')
            return bytes(raw, "utf-8").decode("unicode_escape")
        chars.append(char)
    raise ValueError(f"EastMoney response has unterminated field {key!r}")


def parse_eastmoney_envelope(text: str) -> tuple[str, tuple[int, ...]]:
    """Return embedded holdings HTML and advertised years."""

    content = _extract_js_quoted_field(text, "content")
    years_match = re.search(r"\barryear\s*:\s*\[([^\]]*)\]", text, re.S)
    years: list[int] = []
    if years_match:
        years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", years_match.group(1))]
    return content, tuple(dict.fromkeys(years))


def _quarter_from_heading(heading: str) -> tuple[int, int] | None:
    compact = re.sub(r"\s+", "", heading)
    match = re.search(r"((?:19|20)\d{2})年(?:第)?([1-4])季度", compact)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _report_date(year: int, quarter: int) -> str:
    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    if quarter not in month_day:
        raise ValueError(f"invalid quarter: {quarter}")
    return f"{year:04d}-{month_day[quarter]}"


def _symbols_from_rows(rows: Iterable[list[str]]) -> tuple[str, ...]:
    symbols: set[str] = set()
    for row in rows:
        for cell in row[:4]:
            match = re.search(r"(?<!\d)(\d{6})(?!\d)", cell)
            if match:
                symbols.add(match.group(1))
                break
    return tuple(sorted(symbols))


def parse_holdings_html(
    html: str,
    *,
    fund_code: str,
    source_url: str,
    response_sha256: str,
    minimum_full_report_symbols: int = 11,
) -> list[FundHoldingBatch]:
    """Parse fund disclosure batches for candidate-pool/cross-check use only.

    Q2/Q4 disclosures with more than ten distinct stock codes are labelled
    ``full_report_candidate_set`` because they contain substantially more than
    the ordinary quarterly top-ten disclosure.  They are **never** labelled an
    index anchor: a tracking ETF can hold IPO allocations, substitutions and
    other non-index stocks.  Independent dated index-membership evidence is
    required to filter/validate these candidate sets.
    """

    if minimum_full_report_symbols < 11:
        raise ValueError("minimum_full_report_symbols must be >= 11")
    parser = _FundBoxParser()
    parser.feed(html)
    out: list[FundHoldingBatch] = []
    for heading, rows in parser.boxes:
        parsed = _quarter_from_heading(heading)
        if parsed is None:
            continue
        year, quarter = parsed
        symbols = _symbols_from_rows(rows)
        full_report_candidate_set = (
            quarter in {2, 4} and len(symbols) >= minimum_full_report_symbols
        )
        evidence_kind = (
            "eastmoney_tiantian_full_fund_holdings_candidate_set"
            if full_report_candidate_set
            else "eastmoney_tiantian_partial_holdings_crosscheck_only"
        )
        out.append(
            FundHoldingBatch(
                fund_code=fund_code,
                report_date=_report_date(year, quarter),
                quarter=quarter,
                heading=heading,
                symbols=symbols,
                source_url=source_url,
                response_sha256=response_sha256,
                evidence_kind=evidence_kind,
                full_report_candidate_set=full_report_candidate_set,
            )
        )
    return sorted(out, key=lambda item: item.report_date)


def holdings_url(fund_code: str, year: int, *, topline: int = 100) -> str:
    if not re.fullmatch(r"\d{6}", fund_code):
        raise ValueError("fund_code must be six digits")
    if not 1990 <= int(year) <= 2100:
        raise ValueError("year outside supported range")
    if not 10 <= int(topline) <= 1000:
        raise ValueError("topline outside supported range")
    query = urlencode(
        {
            "type": "jjcc",
            "code": fund_code,
            "topline": int(topline),
            "year": int(year),
            "month": "",
            "rt": "0.731152",
        }
    )
    return f"{EASTMONEY_FUND_HOLDINGS_URL}?{query}"


def fetch_holdings_year(
    fund_code: str,
    year: int,
    *,
    topline: int = 100,
    timeout: int = 30,
) -> tuple[str, str, str]:
    """Fetch one year and return decoded body, URL, and SHA256 of raw bytes."""

    url = holdings_url(fund_code, year, topline=topline)
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,*/*;q=0.8",
            "Referer": EASTMONEY_FUND_PAGE.format(code=fund_code),
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
            ),
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public provider
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    if not raw:
        raise ValueError("EastMoney returned an empty holdings response")
    try:
        text = raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")
    return text, url, sha256(raw).hexdigest()


def fetch_and_parse_holdings_year(
    fund_code: str,
    year: int,
    *,
    topline: int = 100,
    timeout: int = 30,
) -> tuple[list[FundHoldingBatch], tuple[int, ...], str]:
    text, url, digest = fetch_holdings_year(
        fund_code,
        year,
        topline=topline,
        timeout=timeout,
    )
    html, years = parse_eastmoney_envelope(text)
    batches = parse_holdings_html(
        html,
        fund_code=fund_code,
        source_url=url,
        response_sha256=digest,
    )
    return batches, years, text
