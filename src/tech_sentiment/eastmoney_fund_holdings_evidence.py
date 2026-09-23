from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import time
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EASTMONEY_FUND_HOLDINGS_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
EASTMONEY_FUND_PAGE = "https://fundf10.eastmoney.com/ccmx_{code}.html"
TRACKING_ETF_931152 = "159992"
WEIGHTED_CANDIDATE_QUALIFICATION_STATE = "CANDIDATE_ONLY_PUBLICATION_DATE_UNVERIFIED"
WEIGHTED_CANDIDATE_BLOCKER = "PUBLICATION_DATE_NOT_ESTABLISHED"


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


@dataclass(frozen=True)
class FundHoldingWeightCandidate:
    symbol: str
    weight_fraction: float
    raw_weight_text: str


@dataclass(frozen=True)
class FundHoldingWeightedBatch:
    """Candidate-only fund disclosure weights for later look-through qualification.

    This object deliberately does not carry a publication date and can never be
    PIT-qualified by this parser alone.  Report date is not publication date.
    """

    fund_code: str
    report_date: str
    quarter: int
    heading: str
    symbol_count: int
    positions: tuple[FundHoldingWeightCandidate, ...]
    weighted_symbol_count: int
    all_symbol_weights_parsed: bool
    source_url: str
    response_sha256: str
    evidence_kind: str
    full_report_candidate_set: bool
    qualification_state: str = WEIGHTED_CANDIDATE_QUALIFICATION_STATE
    pit_qualified: bool = False


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


def _symbol_from_row(row: Iterable[str]) -> str | None:
    for cell in list(row)[:4]:
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", cell)
        if match:
            return match.group(1)
    return None


def _symbols_from_rows(rows: Iterable[list[str]]) -> tuple[str, ...]:
    symbols = {symbol for row in rows if (symbol := _symbol_from_row(row)) is not None}
    return tuple(sorted(symbols))


def _weight_candidate_from_row(row: list[str]) -> FundHoldingWeightCandidate | None:
    symbol = _symbol_from_row(row)
    if symbol is None:
        return None

    percent_values: list[tuple[str, float]] = []
    for cell in row:
        compact = _clean_text(cell).replace(" ", "")
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", compact)
        if not match:
            continue
        percent = float(match.group(1))
        if 0.0 < percent <= 100.0:
            percent_values.append((compact, percent / 100.0))

    # A row with zero or multiple percentage cells is ambiguous.  Keep the
    # symbol in the candidate set but do not manufacture a portfolio weight.
    if len(percent_values) != 1:
        return None
    raw_weight_text, weight_fraction = percent_values[0]
    return FundHoldingWeightCandidate(
        symbol=symbol,
        weight_fraction=weight_fraction,
        raw_weight_text=raw_weight_text,
    )


def _evidence_kind(*, quarter: int, symbol_count: int, minimum_full_report_symbols: int) -> tuple[str, bool]:
    full_report_candidate_set = quarter in {2, 4} and symbol_count >= minimum_full_report_symbols
    evidence_kind = (
        "eastmoney_tiantian_full_fund_holdings_candidate_set"
        if full_report_candidate_set
        else "eastmoney_tiantian_partial_holdings_crosscheck_only"
    )
    return evidence_kind, full_report_candidate_set


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
        evidence_kind, full_report_candidate_set = _evidence_kind(
            quarter=quarter,
            symbol_count=len(symbols),
            minimum_full_report_symbols=minimum_full_report_symbols,
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


def parse_weighted_holdings_candidates(
    html: str,
    *,
    fund_code: str,
    source_url: str,
    response_sha256: str,
    minimum_full_report_symbols: int = 11,
) -> list[FundHoldingWeightedBatch]:
    """Parse weight-bearing candidate rows without granting PIT qualification.

    EastMoney's table can expose a percentage that is useful for portfolio
    look-through candidate collection.  The parser accepts a weight only when a
    stock row has exactly one unambiguous percentage cell.  Report date is kept
    separate from publication date; because this endpoint does not establish
    the latter, every returned batch remains candidate-only.
    """

    if minimum_full_report_symbols < 11:
        raise ValueError("minimum_full_report_symbols must be >= 11")
    parser = _FundBoxParser()
    parser.feed(html)
    out: list[FundHoldingWeightedBatch] = []
    for heading, rows in parser.boxes:
        parsed = _quarter_from_heading(heading)
        if parsed is None:
            continue
        year, quarter = parsed
        symbols = _symbols_from_rows(rows)
        candidates = [candidate for row in rows if (candidate := _weight_candidate_from_row(row)) is not None]
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.symbol in seen:
                raise ValueError("duplicate weighted symbol in one fund disclosure")
            seen.add(candidate.symbol)
        positions = tuple(sorted(candidates, key=lambda item: item.symbol))
        evidence_kind, full_report_candidate_set = _evidence_kind(
            quarter=quarter,
            symbol_count=len(symbols),
            minimum_full_report_symbols=minimum_full_report_symbols,
        )
        out.append(
            FundHoldingWeightedBatch(
                fund_code=fund_code,
                report_date=_report_date(year, quarter),
                quarter=quarter,
                heading=heading,
                symbol_count=len(symbols),
                positions=positions,
                weighted_symbol_count=len(positions),
                all_symbol_weights_parsed=bool(symbols) and len(positions) == len(symbols),
                source_url=source_url,
                response_sha256=response_sha256,
                evidence_kind=evidence_kind,
                full_report_candidate_set=full_report_candidate_set,
            )
        )
    return sorted(out, key=lambda item: item.report_date)


def lookthrough_candidate_rows(batch: FundHoldingWeightedBatch) -> list[dict[str, object]]:
    """Export non-qualified rows for a downstream private provenance gate.

    The authoritative field name ``weight_within_parent`` is intentionally not
    emitted.  A private gate must verify publication timing and explicitly
    promote a candidate value before Portfolio Look-through V1 can consume it.
    """

    return [
        {
            "fund_code": batch.fund_code,
            "security_id": f"CN:{position.symbol}",
            "weight_within_parent_candidate": position.weight_fraction,
            "raw_weight_text": position.raw_weight_text,
            "report_date": batch.report_date,
            "source_kind": batch.evidence_kind,
            "source_reference": batch.source_url,
            "response_sha256": batch.response_sha256,
            "full_report_candidate_set": batch.full_report_candidate_set,
            "source_published_on": None,
            "pit_qualified": False,
            "qualification_state": batch.qualification_state,
            "qualification_blocker": WEIGHTED_CANDIDATE_BLOCKER,
        }
        for position in batch.positions
    ]


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
    retries: int = 4,
    retry_backoff_seconds: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    opener: Callable[..., object] = urlopen,
) -> tuple[str, str, str]:
    """Fetch one year and return decoded body, URL, and SHA256 of raw bytes.

    Only transport/server failures are retried.  Client-side HTTP errors and
    semantic parsing remain fail-closed so this helper cannot weaken evidence
    qualification rules.
    """

    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if timeout <= 0:
        raise ValueError("timeout must be > 0")

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
            "Connection": "close",
        },
        method="GET",
    )

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with opener(request, timeout=timeout) as response:  # type: ignore[attr-defined]  # noqa: S310
                raw = response.read()  # type: ignore[attr-defined]
                charset = response.headers.get_content_charset() or "utf-8"  # type: ignore[attr-defined]
            if not raw:
                raise URLError("EastMoney returned an empty holdings response")
            try:
                text = raw.decode(charset)
            except (LookupError, UnicodeDecodeError):
                text = raw.decode("utf-8", errors="replace")
            return text, url, sha256(raw).hexdigest()
        except HTTPError as exc:
            last_error = exc
            if 400 <= exc.code < 500 or attempt >= retries:
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        if retry_backoff_seconds:
            sleep_fn(retry_backoff_seconds * (attempt + 1))

    raise RuntimeError("EastMoney holdings fetch exhausted retries") from last_error


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
