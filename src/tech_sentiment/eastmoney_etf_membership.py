from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import unescape
import json
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EASTMONEY_HOLDINGS_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
TRACKING_ETF = "159992"
FULL_DISCLOSURE_MONTHS = "6,12"
QUARTERLY_DISCLOSURE_CAP = 15


@dataclass(frozen=True)
class EastmoneyArchive:
    source_url: str
    raw_text: str
    raw_sha256: str
    available_years: tuple[int, ...]
    current_year: int | None
    content_html: str


@dataclass(frozen=True)
class EastmoneyHoldingPeriod:
    as_of: str
    label: str
    fund_report_symbols: tuple[str, ...]
    cross_reference_symbols: tuple[str, ...]
    expandable: bool
    coverage_status: str

    @property
    def fund_report_count(self) -> int:
        return len(self.fund_report_symbols)

    @property
    def cross_reference_count(self) -> int:
        return len(self.cross_reference_symbols)

    @property
    def is_full_anchor_candidate(self) -> bool:
        return self.coverage_status == "candidate_full_portfolio"


def build_holdings_url(
    *,
    year: int,
    fund_code: str = TRACKING_ETF,
    months: str = FULL_DISCLOSURE_MONTHS,
) -> str:
    if year < 2000 or year > 2100:
        raise ValueError("year outside supported range")
    if not re.fullmatch(r"\d{6}", fund_code):
        raise ValueError("fund_code must be six digits")
    params = {
        "type": "jjcc",
        "code": fund_code,
        "topline": "100",
        "year": str(year),
        "month": months,
        "rt": "0.731529",
    }
    return f"{EASTMONEY_HOLDINGS_URL}?{urlencode(params)}"


def _headers(fund_code: str) -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Referer": f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        ),
    }


def fetch_holdings_archive(
    *,
    year: int,
    fund_code: str = TRACKING_ETF,
    months: str = FULL_DISCLOSURE_MONTHS,
    timeout: int = 30,
) -> EastmoneyArchive:
    url = build_holdings_url(year=year, fund_code=fund_code, months=months)
    request = Request(url, headers=_headers(fund_code), method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public host
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")
    content, years, current_year = parse_apidata(text)
    return EastmoneyArchive(
        source_url=url,
        raw_text=text,
        raw_sha256=sha256(raw).hexdigest(),
        available_years=years,
        current_year=current_year,
        content_html=content,
    )


def _decode_js_string(value: str) -> str:
    # Eastmoney normally emits a JSON-compatible double-quoted string.  Some
    # historical responses escape apostrophes as JavaScript (\') rather than
    # JSON, so normalise only that escape before using the standard decoder.
    normalised = value.replace("\\'", "'")
    try:
        return json.loads(f'"{normalised}"')
    except json.JSONDecodeError as exc:
        raise ValueError("unable to decode Eastmoney apidata content string") from exc


def parse_apidata(text: str) -> tuple[str, tuple[int, ...], int | None]:
    if not text.strip():
        raise ValueError("empty Eastmoney response")
    content_match = re.search(
        r'content\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*arryear\s*:\s*\[([^\]]*)\]',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not content_match:
        raise ValueError("Eastmoney apidata envelope missing content/arryear")
    content = _decode_js_string(content_match.group(1))
    years = tuple(
        sorted(
            {
                int(value)
                for value in re.findall(r"\b(?:19|20)\d{2}\b", content_match.group(2))
            },
            reverse=True,
        )
    )
    current_match = re.search(r"\bcuryear\s*:\s*((?:19|20)\d{2})\b", text)
    current_year = int(current_match.group(1)) if current_match else None
    return content, years, current_year


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _period_blocks(content_html: str) -> Iterable[str]:
    starts = [match.start() for match in re.finditer(r"<div\s+class=['\"]box['\"]", content_html, re.I)]
    if not starts:
        return ()
    blocks: list[str] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(content_html)
        blocks.append(content_html[start:end])
    return tuple(blocks)


def _row_symbol(row_html: str) -> str:
    match = re.search(r"unify/r/[01]\.(\d{6})", row_html, flags=re.I)
    if match:
        return match.group(1)
    match = re.search(r">\s*(\d{6})\s*</a>", row_html, flags=re.I)
    return match.group(1) if match else ""


def _row_sequence(row_html: str) -> str:
    match = re.search(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
    return _clean_text(match.group(1)) if match else ""


def parse_holding_periods(content_html: str) -> list[EastmoneyHoldingPeriod]:
    periods: list[EastmoneyHoldingPeriod] = []
    for block in _period_blocks(content_html):
        as_of_match = re.search(
            r"截止至\s*[：:]?\s*<font\b[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</font>",
            block,
            flags=re.I | re.S,
        )
        if not as_of_match:
            continue
        as_of = as_of_match.group(1)
        label_match = re.search(r"((?:19|20)\d{2}年[1-4]季度股票投资明细)", block)
        label = label_match.group(1) if label_match else ""
        fund_symbols: set[str] = set()
        cross_symbols: set[str] = set()
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", block, flags=re.I | re.S):
            symbol = _row_symbol(row)
            if not symbol:
                continue
            sequence = _row_sequence(row)
            if "*" in sequence:
                cross_symbols.add(symbol)
            else:
                fund_symbols.add(symbol)
        expandable = "显示全部持仓明细" in block
        is_full_period = as_of.endswith("-06-30") or as_of.endswith("-12-31")
        if is_full_period and not expandable and len(fund_symbols) > QUARTERLY_DISCLOSURE_CAP:
            coverage = "candidate_full_portfolio"
        elif is_full_period and expandable:
            coverage = "withheld_top_n"
        else:
            coverage = "partial_disclosure_only"
        periods.append(
            EastmoneyHoldingPeriod(
                as_of=as_of,
                label=label,
                fund_report_symbols=tuple(sorted(fund_symbols)),
                cross_reference_symbols=tuple(sorted(cross_symbols)),
                expandable=expandable,
                coverage_status=coverage,
            )
        )
    return sorted(periods, key=lambda item: item.as_of)
