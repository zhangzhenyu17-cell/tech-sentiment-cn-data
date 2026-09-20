from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

import pandas as pd

from .universe import normalize_symbol


_EFFECTIVE_PATTERNS = (
    re.compile(r"于\s*(\d{4})年(\d{1,2})月(\d{1,2})日.*?生效"),
    re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2}).*?生效"),
)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_NOTICE_RE = re.compile(r"/(?:bse_indices_news|important_news)/(\d+)\.html", re.I)
_ATTACHMENT_SUFFIXES = (".xlsx", ".xls", ".csv")


@dataclass(frozen=True)
class OfficialSnapshot:
    effective_date: pd.Timestamp
    symbols: tuple[str, ...]
    notice_url: str
    attachment_url: str


def extract_effective_date(text: str) -> pd.Timestamp:
    compact = re.sub(r"\s+", " ", text or "")
    for pattern in _EFFECTIVE_PATTERNS:
        match = pattern.search(compact)
        if match:
            year, month, day = map(int, match.groups())
            return pd.Timestamp(year=year, month=month, day=day).normalize()
    raise ValueError("official notice does not expose an effective date")


def discover_notice_links(html: str, *, base_url: str) -> list[str]:
    out: set[str] = set()
    for href in _HREF_RE.findall(html or ""):
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc not in {"www.bse.cn", "bse.cn"}:
            continue
        if _NOTICE_RE.search(parsed.path):
            out.add(absolute.split("#", 1)[0])
    return sorted(out)


def discover_attachment_links(html: str, *, base_url: str) -> list[str]:
    out: set[str] = set()
    for href in _HREF_RE.findall(html or ""):
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc not in {"www.bse.cn", "bse.cn"}:
            continue
        if parsed.path.lower().endswith(_ATTACHMENT_SUFFIXES):
            out.add(absolute)
    return sorted(out)


def _candidate_code_columns(frame: pd.DataFrame) -> list[str]:
    preferred = []
    for column in frame.columns:
        label = str(column).strip()
        if any(token in label for token in ("证券代码", "成份股代码", "样本代码", "股票代码", "代码")):
            preferred.append(column)
    return preferred or list(frame.columns)


def _extract_codes(frame: pd.DataFrame) -> list[str]:
    for column in _candidate_code_columns(frame):
        values: list[str] = []
        for value in frame[column].dropna().tolist():
            digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
            if len(digits) < 6:
                continue
            code = normalize_symbol(digits[-6:])
            if code.startswith(("4", "8", "92")):
                values.append(code)
        unique = list(dict.fromkeys(values))
        if len(unique) >= 50:
            return unique
    return []


def parse_snapshot_attachment(payload: bytes, *, filename: str) -> tuple[str, ...]:
    candidates: list[tuple[str, pd.DataFrame]] = []
    lower = filename.lower().split("?", 1)[0]
    if lower.endswith(".csv"):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                candidates.append(("csv", pd.read_csv(StringIO(payload.decode(encoding)))))
                break
            except Exception:
                continue
    else:
        workbook = pd.ExcelFile(BytesIO(payload))
        for sheet in workbook.sheet_names:
            candidates.append((str(sheet), pd.read_excel(workbook, sheet_name=sheet)))

    exact: list[tuple[str, tuple[str, ...]]] = []
    oversized: list[str] = []
    for name, frame in candidates:
        codes = tuple(_extract_codes(frame))
        if len(codes) == 50:
            exact.append((name, codes))
        elif len(codes) > 50:
            oversized.append(name)

    if len(exact) == 1:
        return exact[0][1]
    if len(exact) > 1:
        preferred = [item for item in exact if any(t in item[0] for t in ("样本", "成份", "成分"))]
        if len(preferred) == 1:
            return preferred[0][1]
        raise ValueError(f"ambiguous attachment: multiple exact-50 sheets {[x[0] for x in exact]}")
    if oversized:
        raise ValueError(f"attachment has >50 BSE codes without an exact-50 sample sheet: {oversized}")
    raise ValueError("attachment does not expose an exact 50-code BSE50 sample list")


def snapshots_to_membership(
    snapshots: Iterable[OfficialSnapshot],
    *,
    history_start: str | pd.Timestamp,
    history_end: str | pd.Timestamp,
    index_code: str = "899050",
    limit_pct: float = 30.0,
) -> pd.DataFrame:
    start = pd.Timestamp(history_start).normalize()
    end = pd.Timestamp(history_end).normalize()
    ordered = sorted(snapshots, key=lambda x: x.effective_date)
    if not ordered:
        raise ValueError("no official BSE50 snapshots")
    if ordered[0].effective_date > start:
        raise ValueError("first official snapshot starts after requested history_start")

    rows: list[dict[str, object]] = []
    for idx, snapshot in enumerate(ordered):
        if len(set(snapshot.symbols)) != 50:
            raise ValueError(f"{snapshot.effective_date.date()}: snapshot is not exactly 50 symbols")
        seg_start = max(start, snapshot.effective_date)
        next_start = ordered[idx + 1].effective_date if idx + 1 < len(ordered) else end + pd.Timedelta(days=1)
        seg_end = min(end, next_start - pd.Timedelta(days=1))
        if seg_start > seg_end:
            continue
        for symbol in snapshot.symbols:
            rows.append(
                {
                    "symbol": normalize_symbol(symbol),
                    "effective_start": seg_start,
                    "effective_end": seg_end,
                    "universe_mode": "point_in_time",
                    "source_index": index_code,
                    "board": "beijing",
                    "limit_pct": float(limit_pct),
                    "source_notice_url": snapshot.notice_url,
                    "source_attachment_url": snapshot.attachment_url,
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("official snapshots produced no membership rows")
    return out.sort_values(["effective_start", "symbol"]).reset_index(drop=True)
