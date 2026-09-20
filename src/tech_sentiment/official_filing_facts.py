from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import gzip
import io
import json
import re
import unicodedata
from typing import Callable, Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pandas as pd
import requests

from .bounded_retry import call_with_bounded_network_retry
from .pit_public_materialization import (
    REQUIRED_PIT_COLUMNS,
    _stable_hash,
    validate_materialized_pit_records,
)


DERIVED_FUNDAMENTAL_SOURCE_ID = "DERIVED_PIT_FUNDAMENTAL_TRENDS"
DERIVED_FUNDAMENTAL_PROVIDER = "DERIVED_VERSIONED_OFFICIAL_FILINGS"
LEGACY_FILING_PARSER_VERSION = "official-filing-facts-v8-unicode-multiengine-safe-units-revision-time"
FILING_PARSER_VERSION = "official-filing-facts-v9-explicit-unit-scaling-layout-labels"

FILING_FACT_COLUMNS = (
    "entity_id",
    "period_end",
    "fact_type",
    "value",
    "unit",
    "evidence_available_date",
    "publication_timestamp",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "document_url",
    "document_sha256",
    "parser_version",
)
DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS = tuple(REQUIRED_PIT_COLUMNS) + (
    "evidence_payload",
    "source_url_identity",
)

_OFFICIAL_ATTACHMENT_HOSTS = {
    "static.cninfo.com.cn",
    "www.cninfo.com.cn",
    "www.sse.com.cn",
    "static.sse.com.cn",
    "star.sse.com.cn",
    "disc.static.szse.cn",
    "www.szse.cn",
}

_FACT_LABELS: dict[str, tuple[str, ...]] = {
    "OPERATING_REVENUE": ("营业收入",),
    "NET_PROFIT_PARENT": (
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于母公司所有者的净利润",
    ),
    "OPERATING_CASH_FLOW_NET": ("经营活动产生的现金流量净额",),
    "TOTAL_ASSETS": ("总资产",),
    "EQUITY_PARENT": (
        "归属于上市公司股东的所有者权益",
        "归属于母公司所有者权益",
    ),
    "BASIC_EPS": ("基本每股收益",),
}
_UNIT_RE = re.compile(
    r"单位:(?:人民币)?(亿元|百万元|万元|千元|元)"
    r"(?=$|币种|金额|[,:;()。])"
)
_AMOUNT_UNIT_SCALE = {
    "元": 1.0,
    "千元": 1_000.0,
    "万元": 10_000.0,
    "百万元": 1_000_000.0,
    "亿元": 100_000_000.0,
}
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\d.])(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))(?![\d.])"
)

_FILING_PRESENTATION_TITLE_RE = re.compile(
    r"20\d{2}年(?:年度报告|半年度报告|第一季度报告|一季度报告|第三季度报告|三季度报告)"
    r"(?:（?(?P<variant>摘要|正文|全文)）?)?"
)
FILING_PRESENTATION_FULL = "FULL_OR_CANONICAL"
FILING_PRESENTATION_BODY = "BODY"
FILING_PRESENTATION_SUMMARY = "SUMMARY"
FILING_PRESENTATION_UNKNOWN = "UNKNOWN"


def classify_official_filing_presentation(text: object) -> str:
    """Classify the official PDF's own report-title carrier from its leading text.

    Historical PDFs can render a generic running header such as
    "2021年第一季度报告" before the actual cover/title carrier
    "2021年第一季度报告正文". A first-match-only classifier therefore
    misclassifies BODY as FULL. Explicit presentation markers in the leading
    title region take precedence over a preceding generic header. Conflicting
    explicit markers remain UNKNOWN rather than guessing.
    """

    compact = re.sub(r"\s+", "", str(text or ""))[:5000]
    matches = list(_FILING_PRESENTATION_TITLE_RE.finditer(compact))
    if not matches:
        return FILING_PRESENTATION_UNKNOWN

    # Presentation markers that identify the carrier appear on the cover/title
    # region. Limit explicit-marker precedence to the leading region so later
    # references/table-of-contents text cannot relabel a full report.
    leading = compact[:1200]
    explicit = {
        str(match.group("variant") or "")
        for match in _FILING_PRESENTATION_TITLE_RE.finditer(leading)
        if str(match.group("variant") or "")
    }
    if len(explicit) > 1:
        return FILING_PRESENTATION_UNKNOWN
    if explicit == {"摘要"}:
        return FILING_PRESENTATION_SUMMARY
    if explicit == {"正文"}:
        return FILING_PRESENTATION_BODY
    if explicit == {"全文"}:
        return FILING_PRESENTATION_FULL
    return FILING_PRESENTATION_FULL


@dataclass(frozen=True)
class DownloadedOfficialDocument:
    # Canonical immutable source identity requested by the materializer.
    url: str
    # Hash of the decoded exact official document bytes consumed by parsers.
    sha256: str
    content: bytes
    # Actual same-provider HTTPS transport endpoint used to retrieve bytes.
    # Optional keeps existing injected test doubles/backward-compatible callers valid.
    retrieval_url: str | None = None
    # Raw HTTP entity-body identity before transport decoding. This is distinct
    # from sha256 only when an official host returns a gzip-wrapped PDF body.
    transport_sha256: str | None = None
    transport_encoding: str | None = None
    transport_method: str | None = None


_OFFICIAL_PROVIDER_HOST_FAMILIES = {
    "CNINFO": {"static.cninfo.com.cn", "www.cninfo.com.cn"},
    "SSE": {"www.sse.com.cn", "static.sse.com.cn", "star.sse.com.cn"},
    "SZSE": {"www.szse.cn", "disc.static.szse.cn"},
}

_EXCHANGE_ATTACHMENT_BROWSER_PROFILES = {
    "SSE": {
        "referer": "https://www.sse.com.cn/disclosure/listedinfo/announcement/",
        "bootstrap": "https://www.sse.com.cn/disclosure/listedinfo/announcement/",
    },
    "SZSE": {
        "referer": "https://www.szse.cn/disclosure/listed/notice/index.html",
        "bootstrap": "https://www.szse.cn/disclosure/listed/notice/index.html",
    },
}


def _official_provider_family(host: str) -> str:
    for family, hosts in _OFFICIAL_PROVIDER_HOST_FAMILIES.items():
        if host in hosts:
            return family
    raise ValueError(f"official filing host has no provider family: {host}")


def _validate_same_provider_retrieval(canonical_url: str, retrieval_url: str) -> None:
    canonical_host = _canonical_host(canonical_url)
    retrieval_host = _canonical_host(retrieval_url)
    if _official_provider_family(canonical_host) != _official_provider_family(retrieval_host):
        raise ValueError(
            "official filing retrieval redirected outside the canonical provider family"
        )


def _looks_like_pdf(content: bytes) -> bool:
    return content.lstrip(b"\xef\xbb\xbf\r\n\t ").startswith(b"%PDF-")


def _looks_like_html(content: bytes) -> bool:
    prefix = content.lstrip(b"\xef\xbb\xbf\r\n\t ").lower()[:256]
    return (
        prefix.startswith(b"<html")
        or prefix.startswith(b"<!doctype html")
        or b"<html" in prefix
    )


def _canonical_host(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("official filing URL must be absolute HTTPS")
    host = parsed.hostname.lower()
    if host not in _OFFICIAL_ATTACHMENT_HOSTS:
        raise ValueError(f"filing attachment host is not allowlisted: {host}")
    return host


_CNINFO_STATIC_ATTACHMENT_RE = re.compile(
    r"^/finalpage/(?P<date>20\d{2}-\d{2}-\d{2})/(?P<document_id>\d+)\.PDF$",
    re.IGNORECASE,
)


def _cninfo_https_download_fallback(url: str) -> str | None:
    """Derive CNINFO's official HTTPS download endpoint from an exact attachment URL."""

    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "static.cninfo.com.cn":
        return None
    match = _CNINFO_STATIC_ATTACHMENT_RE.fullmatch(parsed.path)
    if match is None:
        return None
    query = urlencode(
        {
            "bulletinId": match.group("document_id"),
            "announceTime": match.group("date"),
        }
    )
    return f"https://www.cninfo.com.cn/new/announcement/download?{query}"


_SSE_LISTED_ATTACHMENT_RE = re.compile(
    r"^/disclosure/listedinfo/announcement/c/new/.+\.pdf$",
    re.IGNORECASE,
)


def _sse_star_attachment_fallback(url: str) -> str | None:
    """Map one exact STAR-market listed PDF path to SSE's STAR HTTPS host.

    Historical public evidence keeps the canonical www.sse.com.cn identity.
    For 688xxx listed-company announcement PDFs, SSE's STAR disclosure site
    serves the exact same path over HTTPS. Only the host changes; path, query
    and document filename remain byte-for-byte identical.
    """

    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in {"www.sse.com.cn", "static.sse.com.cn"}
        or _SSE_LISTED_ATTACHMENT_RE.fullmatch(parsed.path) is None
    ):
        return None
    filename = parsed.path.rsplit("/", 1)[-1]
    if re.match(r"^688\d{3}_", filename, flags=re.IGNORECASE) is None:
        return None
    return parsed._replace(netloc="star.sse.com.cn").geturl()


def _sse_static_attachment_fallback(url: str) -> str | None:
    """Map one exact SSE listed-company PDF path to SSE's static attachment host.

    The SSE announcement query API can expose a relative path that historical
    materialization bound to www.sse.com.cn. SSE's own announcement full-text
    links serve the same path from static.sse.com.cn. This is a transport-only
    same-provider mapping: the canonical evidence URL remains unchanged and
    provenance records the actual retrieval URL separately.
    """

    parsed = urlparse(str(url).strip())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.sse.com.cn"
        or _SSE_LISTED_ATTACHMENT_RE.fullmatch(parsed.path) is None
    ):
        return None
    return parsed._replace(netloc="static.sse.com.cn").geturl()


def _download_once(
    url: str,
    *,
    timeout: float,
    opener: Callable[..., object],
) -> bytes:
    host = _canonical_host(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
    }
    if host in {"static.cninfo.com.cn", "www.cninfo.com.cn"}:
        headers["Referer"] = "https://www.cninfo.com.cn/"
    request = Request(url, headers=headers, method="GET")
    with opener(request, timeout=timeout) as response:  # nosec B310 - host allowlist above
        return response.read()



_CNINFO_SESSION_BOOTSTRAP_URL = "https://www.cninfo.com.cn/"


def _download_cninfo_with_https_session(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Retry the same CNINFO bulletin over browser-like HTTPS session transport.

    This transport fallback is used only after both urllib HTTPS paths returned
    403.  It never changes the canonical source identity, never downgrades to
    HTTP, and rejects redirects outside the existing official HTTPS allowlist.
    """

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cninfo.com.cn/",
    }
    bootstrap_headers = {
        "User-Agent": headers["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": headers["Accept-Language"],
    }
    session = requests.Session()
    try:
        try:
            bootstrap = call_with_bounded_network_retry(
                lambda: session.get(
                    _CNINFO_SESSION_BOOTSTRAP_URL,
                    headers=bootstrap_headers,
                    timeout=min(timeout, 10.0),
                    allow_redirects=True,
                ),
                attempts=2,
                backoff_seconds=0.5,
            )
            if int(getattr(bootstrap, "status_code", 0) or 0) >= 500:
                bootstrap.raise_for_status()
        except (requests.RequestException, ValueError):
            # Cookie warm-up is best-effort only; the exact bulletin request
            # below remains authoritative and fail-closed.
            pass

        for candidate in (canonical_url, fallback_url):
            response = call_with_bounded_network_retry(
                lambda candidate=candidate: session.get(
                    candidate,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                ),
                attempts=3,
                backoff_seconds=0.5,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 403:
                continue
            response.raise_for_status()
            retrieval_url = str(getattr(response, "url", candidate) or candidate)
            _canonical_host(retrieval_url)
            content = bytes(response.content)
            if not content:
                raise ValueError("official filing attachment is empty")
            return content, retrieval_url
    finally:
        session.close()

    raise HTTPError(
        fallback_url,
        403,
        "Forbidden after CNINFO HTTPS session transport",
        hdrs=None,
        fp=None,
    )


def _download_cninfo_with_browser_transport(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Use browser-fingerprint HTTPS transport against the same CNINFO bulletin.

    This is a final transport-only fallback for hosted-runner WAF decisions.
    Canonical evidence identity remains the immutable CNINFO attachment URL.
    Both candidate URLs stay on the existing official HTTPS allowlist, and any
    redirect outside that allowlist is rejected before bytes are accepted.
    """

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - installed by the data extra via akshare
        raise RuntimeError(
            "curl_cffi is required for CNINFO browser transport fallback"
        ) from exc

    headers = {
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cninfo.com.cn/",
    }
    last_error: Exception | None = None
    for candidate in (canonical_url, fallback_url):
        try:
            response = curl_requests.get(
                candidate,
                headers=headers,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 403:
                last_error = HTTPError(
                    candidate, 403, "Forbidden", hdrs=None, fp=None
                )
                continue
            response.raise_for_status()
            retrieval_url = str(getattr(response, "url", candidate) or candidate)
            _canonical_host(retrieval_url)
            content = bytes(response.content)
            if not content:
                raise ValueError("official filing attachment is empty")
            return content, retrieval_url
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(
            "CNINFO browser HTTPS transport exhausted without official PDF bytes"
        ) from last_error
    raise RuntimeError("CNINFO browser HTTPS transport produced no attempt")


def _download_exchange_attachment_with_browser_transport(
    canonical_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Retry one SSE/SZSE attachment with same-provider browser transport.

    The canonical evidence identity never changes. For historical SSE records
    whose query-API relative path was bound to www.sse.com.cn, the exact same
    path on static.sse.com.cn is an allowed same-provider transport candidate.
    No search, filename substitution, alternate issuer source, or HTTP
    downgrade is permitted.
    """

    canonical_host = _canonical_host(canonical_url)
    family = _official_provider_family(canonical_host)
    profile = _EXCHANGE_ATTACHMENT_BROWSER_PROFILES.get(family)
    if profile is None:
        raise ValueError(
            f"browser attachment fallback is not defined for provider family: {family}"
        )

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - installed by the data extra
        raise RuntimeError(
            "curl_cffi is required for exchange attachment browser transport fallback"
        ) from exc

    headers = {
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": str(profile["referer"]),
    }
    bootstrap_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": headers["Accept-Language"],
    }

    candidates: list[str] = []
    star_fallback = _sse_star_attachment_fallback(canonical_url)
    if star_fallback is not None:
        candidates.append(star_fallback)
    static_fallback = _sse_static_attachment_fallback(canonical_url)
    if static_fallback is not None and static_fallback not in candidates:
        candidates.append(static_fallback)
    candidates.append(canonical_url)

    session = curl_requests.Session()
    last_error: Exception | None = None
    last_html: tuple[bytes, str] | None = None
    try:
        try:
            bootstrap = session.get(
                str(profile["bootstrap"]),
                headers=bootstrap_headers,
                impersonate="chrome",
                timeout=min(timeout, 10.0),
                allow_redirects=True,
            )
            bootstrap_url = str(
                getattr(bootstrap, "url", profile["bootstrap"]) or profile["bootstrap"]
            )
            _validate_same_provider_retrieval(canonical_url, bootstrap_url)
        except Exception:
            # Cookie/bootstrap warm-up is best-effort only. Exact attachment
            # requests below remain authoritative and fail closed.
            pass

        for candidate in candidates:
            try:
                response = call_with_bounded_network_retry(
                    lambda candidate=candidate: session.get(
                        candidate,
                        headers=headers,
                        impersonate="chrome",
                        timeout=timeout,
                        allow_redirects=True,
                    ),
                    attempts=3,
                    backoff_seconds=0.5,
                )
                response.raise_for_status()
                retrieval_url = str(
                    getattr(response, "url", candidate) or candidate
                )
                _validate_same_provider_retrieval(canonical_url, retrieval_url)
                content = bytes(getattr(response, "content", b""))
                if not content:
                    raise ValueError(
                        "official filing attachment browser transport is empty"
                    )
                decoded, _ = _decode_official_transport_body(content)
                if _looks_like_html(decoded):
                    last_html = (content, retrieval_url)
                    continue
                return content, retrieval_url
            except Exception as exc:
                last_error = exc
                continue
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    if last_html is not None:
        return last_html
    if last_error is not None:
        raise RuntimeError(
            "exchange browser transport exhausted same-provider attachment candidates"
        ) from last_error
    raise RuntimeError("exchange browser transport produced no attachment attempt")

def _decode_official_transport_body(content: bytes) -> tuple[bytes, str | None]:
    """Decode transport wrapping without changing the official document identity.

    Some SSE/SZSE attachment endpoints return a gzip-wrapped PDF entity body to
    urllib clients even when the URL itself identifies a PDF. PDF parsers must
    consume the decoded document bytes, while provenance separately preserves
    the raw transport-body hash. Only the deterministic gzip container signaled
    by its RFC 1952 magic bytes is decoded; every other payload is passed
    through unchanged and remains subject to the normal fail-closed PDF parser.
    """

    if content.startswith(b"\x1f\x8b\x08"):
        try:
            decoded = gzip.decompress(content)
        except (OSError, EOFError) as exc:
            raise ValueError("official filing gzip transport body is invalid") from exc
        if not decoded:
            raise ValueError("official filing gzip transport body decoded empty")
        return decoded, "gzip"
    return content, None


def download_official_document(
    url: str,
    *,
    timeout: float = 30.0,
    opener: Callable[..., object] = urlopen,
) -> DownloadedOfficialDocument:
    """Download one exact official filing version and bind it to a content hash.

    CNINFO's immutable static HTTPS attachment can return HTTP 403 to non-browser
    infrastructure even when the same exact bulletin remains available through
    CNINFO's official HTTPS download endpoint.  Only that deterministic,
    same-provider endpoint is allowed as a fallback, and only for a 403 from
    static.cninfo.com.cn.  The bulletin id and announcement date are derived
    from the immutable attachment URL; no search, substitution, or HTTP
    transport downgrade is permitted.
    """

    _canonical_host(url)
    retrieval_url = url
    try:
        content = call_with_bounded_network_retry(
            lambda: _download_once(url, timeout=timeout, opener=opener),
            attempts=3,
            backoff_seconds=0.5,
        )
    except HTTPError as exc:
        fallback = _cninfo_https_download_fallback(url)
        if exc.code != 403 or fallback is None:
            raise
        retrieval_url = fallback
        try:
            content = call_with_bounded_network_retry(
                lambda: _download_once(fallback, timeout=timeout, opener=opener),
                attempts=3,
                backoff_seconds=0.5,
            )
        except HTTPError as fallback_exc:
            if fallback_exc.code != 403:
                raise
            try:
                content, retrieval_url = _download_cninfo_with_https_session(
                    url,
                    fallback,
                    timeout=timeout,
                )
            except HTTPError as session_exc:
                if session_exc.code != 403:
                    raise
                content, retrieval_url = _download_cninfo_with_browser_transport(
                    url,
                    fallback,
                    timeout=timeout,
                )

    if not content:
        raise ValueError("official filing attachment is empty")

    transport_method = "urllib"
    transport_sha256 = sha256(content).hexdigest()
    document_content, transport_encoding = _decode_official_transport_body(content)

    canonical_host = _canonical_host(url)
    provider_family = _official_provider_family(canonical_host)

    if _looks_like_html(document_content) and provider_family == "SSE":
        same_provider_candidates: list[tuple[str, str]] = []
        star_fallback = _sse_star_attachment_fallback(url)
        if star_fallback is not None:
            same_provider_candidates.append(("same_provider_star", star_fallback))
        static_fallback = _sse_static_attachment_fallback(url)
        if static_fallback is not None:
            same_provider_candidates.append(("same_provider_static", static_fallback))

        for candidate_method, candidate_url in same_provider_candidates:
            try:
                fallback_content = call_with_bounded_network_retry(
                    lambda candidate_url=candidate_url: _download_once(
                        candidate_url,
                        timeout=timeout,
                        opener=opener,
                    ),
                    attempts=3,
                    backoff_seconds=0.5,
                )
                fallback_document, fallback_encoding = _decode_official_transport_body(
                    fallback_content
                )
                if _looks_like_pdf(fallback_document):
                    _validate_same_provider_retrieval(url, candidate_url)
                    content = fallback_content
                    retrieval_url = candidate_url
                    transport_method = candidate_method
                    transport_sha256 = sha256(content).hexdigest()
                    document_content = fallback_document
                    transport_encoding = fallback_encoding
                    break
            except Exception:
                # Browser transport below remains the final same-provider
                # fallback. A failed candidate never changes evidence identity
                # or weakens fail-closed behavior.
                continue

    if _looks_like_html(document_content) and provider_family in {"SSE", "SZSE"}:
        content, retrieval_url = _download_exchange_attachment_with_browser_transport(
            url,
            timeout=timeout,
        )
        transport_method = "same_provider_browser"
        transport_sha256 = sha256(content).hexdigest()
        document_content, transport_encoding = _decode_official_transport_body(content)

    if _looks_like_html(document_content):
        raise ValueError(
            "official filing attachment returned HTML instead of PDF after allowed transport fallbacks"
        )
    if not _looks_like_pdf(document_content):
        raise ValueError(
            "official filing attachment returned non-PDF content after allowed transport fallbacks"
        )

    return DownloadedOfficialDocument(
        url=url,
        retrieval_url=retrieval_url,
        sha256=sha256(document_content).hexdigest(),
        content=document_content,
        transport_sha256=transport_sha256,
        transport_encoding=transport_encoding,
        transport_method=transport_method,
    )


def _pdfplumber_text(content: bytes) -> str:
    """Extract only the existing PDF text layer with pdfminer/pdfplumber; no OCR."""

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - installed by the data extra
        raise RuntimeError(
            "pdfplumber is required for secondary official filing text extraction"
        ) from exc

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            if text.strip():
                parts.append(text)
    return "\n".join(parts).strip()


def _pymupdf_text(content: bytes) -> str:
    """Extract only the existing PDF text layer with PyMuPDF; no OCR."""

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - installed by the data extra
        raise RuntimeError(
            "pymupdf is required for tertiary official filing text extraction"
        ) from exc

    parts: list[str] = []
    document = pymupdf.open(stream=content, filetype="pdf")
    try:
        for page in document:
            text = page.get_text("text", sort=True) or ""
            if text.strip():
                parts.append(text)
    finally:
        document.close()
    return "\n".join(parts).strip()


def extract_pdf_text(content: bytes) -> str:
    """Extract the embedded text layer through conservative parser fallbacks.

    Order:
    1. pypdf layout mode;
    2. pypdf ordinary text mode;
    3. pdfplumber/pdfminer;
    4. PyMuPDF.

    A fallback is selected only when it restores an explicit table-unit
    declaration. All paths read the same immutable official PDF bytes. OCR,
    image inference, unit inference and non-official substitute documents are
    deliberately absent.
    """

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf is required for official filing parsing") from exc

    reader = PdfReader(io.BytesIO(content), strict=False)

    def _collect(*, layout: bool) -> str:
        parts: list[str] = []
        for page in reader.pages:
            text = (
                page.extract_text(extraction_mode="layout")
                if layout
                else page.extract_text()
            ) or ""
            if text.strip():
                parts.append(text)
        return "\n".join(parts).strip()

    candidates: list[str] = []

    layout_text = _collect(layout=True)
    if layout_text:
        candidates.append(layout_text)
        if _has_explicit_unit_declaration(_normalize_text_lines(layout_text)):
            return layout_text

    plain_text = _collect(layout=False)
    if plain_text:
        candidates.append(plain_text)
        if _has_explicit_unit_declaration(_normalize_text_lines(plain_text)):
            return plain_text

    miner_text = _pdfplumber_text(content)
    if miner_text:
        candidates.append(miner_text)
        if _has_explicit_unit_declaration(_normalize_text_lines(miner_text)):
            return miner_text

    mupdf_text = _pymupdf_text(content)
    if mupdf_text:
        candidates.append(mupdf_text)
        if _has_explicit_unit_declaration(_normalize_text_lines(mupdf_text)):
            return mupdf_text

    if candidates:
        # Preserve the primary text layer for diagnostics when every parser
        # fails the explicit-unit gate; downstream fact extraction remains
        # fail-closed and will never infer a unit.
        return candidates[0]
    raise ValueError("official filing has no extractable text layer")


def filing_period_end_from_title(title: object) -> pd.Timestamp:
    text = re.sub(r"\s+", "", str(title or ""))
    match = re.search(r"(20\d{2})年", text)
    if not match:
        raise ValueError("filing title lacks a report year")
    year = int(match.group(1))
    if any(token in text for token in ("第一季度", "一季度")):
        return pd.Timestamp(year=year, month=3, day=31)
    if any(token in text for token in ("半年度", "半年报", "中期报告")):
        return pd.Timestamp(year=year, month=6, day=30)
    if any(token in text for token in ("第三季度", "三季度")):
        return pd.Timestamp(year=year, month=9, day=30)
    if any(token in text for token in ("年度报告", "年报")):
        return pd.Timestamp(year=year, month=12, day=31)
    raise ValueError("filing title does not identify a supported report period")


def _normalize_pdf_unicode(value: object) -> str:
    """Normalize compatibility glyphs and strip invisible PDF control artifacts.

    Only Unicode compatibility/control cleanup is performed. Visible CJK text,
    punctuation, digits and signs are preserved; this is not OCR or semantic
    repair.
    """

    normalized = unicodedata.normalize("NFKC", str(value))
    chars: list[str] = []
    for char in normalized:
        if char in {"\n", "\r", "\t"}:
            chars.append(char)
            continue
        if unicodedata.category(char).startswith("C"):
            continue
        chars.append(char)
    return "".join(chars)


def _normalize_text_lines(text: str) -> list[str]:
    clean = (
        _normalize_pdf_unicode(text)
        .replace("，", ",")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("／", "/")
        .replace("−", "-")
        .replace("—", "-")
    )
    return [" ".join(line.split()) for line in clean.splitlines() if line.strip()]


def _parse_numeric_token(token: str) -> float:
    text = token.strip().replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    value = float(text)
    return -value if negative else value


def _explicit_unit_from_text(value: str) -> str | None:
    """Read only an explicit 单位 declaration after Unicode/layout cleanup."""

    compact = re.sub(r"\s+", "", _normalize_pdf_unicode(value))
    compact = compact.replace("：", ":")
    match = _UNIT_RE.search(compact)
    return str(match.group(1)) if match else None


def _nearest_explicit_unit(
    lines: list[str],
    index: int,
    *,
    lookback: int = 20,
    max_unit_lines: int = 5,
) -> str | None:
    """Return the nearest explicit table unit at or before a fact row.

    The bounded lookup avoids using a unit declaration from an unrelated distant
    table. A declaration may span up to three physical PDF-text lines. No unit is
    inferred and no rescaling is done.
    """

    left = max(0, index - lookback)
    for position in range(index, left - 1, -1):
        max_span = min(max_unit_lines, index - position + 1)
        for span in range(1, max_span + 1):
            unit = _explicit_unit_from_text(
                " ".join(lines[position : position + span])
            )
            if unit is not None:
                return unit
    return None


def _has_explicit_unit_declaration(
    lines: list[str],
    *,
    max_unit_lines: int = 5,
) -> bool:
    for position in range(len(lines)):
        for span in range(1, max_unit_lines + 1):
            end = position + span
            if end > len(lines):
                break
            if _explicit_unit_from_text(" ".join(lines[position:end])) is not None:
                return True
    return False


def _logical_row_window(
    lines: list[str],
    index: int,
    *,
    max_lines: int = 6,
) -> str:
    """Rejoin one visually wrapped PDF table row without crossing into later values.

    pypdf layout extraction can split a single Chinese table label across
    physical lines, for example 归属于上市公司 / 股东的净利润. We only
    join forward until the first numeric token appears, capped at four physical
    lines, so labels may be reconstructed without flattening adjacent table rows.
    """

    parts: list[str] = []
    for position in range(index, min(len(lines), index + max_lines)):
        parts.append(lines[position])
        joined = " ".join(parts)
        if _NUMERIC_TOKEN_RE.search(joined):
            break
    return " ".join(parts)


def _compact_row_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value))


def _wrapped_label_match(text: str, labels: Iterable[str]) -> re.Match[str] | None:
    """Match a label even when PDF layout extraction inserts whitespace inside it."""

    for label in labels:
        pattern = re.compile(r"\s*".join(re.escape(char) for char in label))
        match = pattern.search(text)
        if match is not None:
            return match
    return None


def _fragmented_label_value(
    lines: list[str],
    index: int,
    labels: Iterable[str],
    *,
    max_lines: int = 6,
) -> float | None:
    """Recover a table row whose numeric cell splits the visual label itself.

    Some annual-report PDFs place the first numeric cell before the final glyphs
    of a wrapped Chinese label. Normal whitespace-tolerant matching cannot
    recover that layout. This helper is deliberately narrow: the first physical
    line must contain the beginning of the target label, only three physical
    lines are considered, numeric tokens are removed only for label recognition,
    and the original window is retained for value extraction.
    """

    first_line = _compact_row_text(lines[index])
    candidates = [
        label
        for label in labels
        if label[: min(4, len(label))] in first_line
    ]
    if not candidates:
        return None
    window = " ".join(lines[index : min(len(lines), index + max_lines)])
    projection = _compact_row_text(_NUMERIC_TOKEN_RE.sub(" ", window))
    if not any(label in projection for label in candidates):
        return None
    for token in _NUMERIC_TOKEN_RE.findall(window):
        try:
            value = _parse_numeric_token(token)
        except ValueError:
            continue
        if pd.notna(value):
            return float(value)
    return None

def _first_amount_value_after_label(
    lines: list[str],
    labels: Iterable[str],
) -> float | None:
    """Extract a canonical CNY amount only from an explicit amount-unit context.

    Explicit 元/千元/万元/百万元/亿元 declarations are deterministic source
    metadata, so scaling them to CNY is normalization rather than inference.
    Missing or unrecognized units remain fail-closed.
    """

    for index in range(len(lines)):
        logical_row = _logical_row_window(lines, index)
        label_match = _wrapped_label_match(logical_row, labels)
        unit = _nearest_explicit_unit(lines, index)
        scale = _AMOUNT_UNIT_SCALE.get(str(unit or ""))
        if scale is None:
            continue
        if label_match is None:
            fragmented = _fragmented_label_value(lines, index, labels)
            if fragmented is not None:
                return float(fragmented) * scale
            continue
        # Preserve original whitespace after the label so adjacent numeric cells
        # can never be concatenated into one token.
        suffix = logical_row[label_match.end() :]
        for token in _NUMERIC_TOKEN_RE.findall(suffix):
            try:
                value = _parse_numeric_token(token)
            except ValueError:
                continue
            if pd.notna(value):
                return float(value) * scale
    return None


def _first_basic_eps_value(lines: list[str], labels: Iterable[str]) -> float | None:
    """Extract EPS from an explicitly proven per-share row, including wrapped units."""

    for index in range(len(lines)):
        logical_row = _logical_row_window(lines, index)
        label_match = _wrapped_label_match(logical_row, labels)
        if label_match is None:
            continue
        suffix = logical_row[label_match.end() :]
        first_number = _NUMERIC_TOKEN_RE.search(suffix)
        if first_number is None:
            continue
        unit_region = _compact_row_text(suffix[: first_number.start()])
        if "元/股" not in unit_region:
            continue
        try:
            value = _parse_numeric_token(first_number.group(0))
        except ValueError:
            continue
        if pd.notna(value):
            return float(value)

    # Preserve the existing explicit-table-unit contract for legacy layouts in
    # which EPS shares the table's declared 单位：元 context.
    # EPS is a per-share amount and must never inherit 千元/万元/百万元 scaling.
    for index in range(len(lines)):
        logical_row = _logical_row_window(lines, index)
        label_match = _wrapped_label_match(logical_row, labels)
        if label_match is None:
            continue
        if _nearest_explicit_unit(lines, index) != "元":
            continue
        suffix = logical_row[label_match.end() :]
        for token in _NUMERIC_TOKEN_RE.findall(suffix):
            try:
                value = _parse_numeric_token(token)
            except ValueError:
                continue
            if pd.notna(value):
                return float(value)
    return None


def extract_standard_filing_facts(text: str) -> dict[str, float]:
    """Extract facts only from locally proven explicit amount-unit contexts.

    A document may legitimately contain unrelated tables in different units.
    A target amount is accepted only when its nearest bounded declaration is an
    explicit supported CNY amount unit (元/千元/万元/百万元/亿元), then
    deterministically normalized to CNY. Units are never inferred and missing
    facts are never filled.
    """

    lines = _normalize_text_lines(text)
    if not _has_explicit_unit_declaration(lines):
        raise ValueError("filing text does not contain an explicit table unit declaration")

    facts: dict[str, float] = {}
    for fact_type, labels in _FACT_LABELS.items():
        value = (
            _first_basic_eps_value(lines, labels)
            if fact_type == "BASIC_EPS"
            else _first_amount_value_after_label(lines, labels)
        )
        if value is not None:
            facts[fact_type] = value
    if not facts:
        for index in range(len(lines)):
            compact = _compact_row_text(_logical_row_window(lines, index))
            contains_target = any(
                label in compact
                for labels in _FACT_LABELS.values()
                for label in labels
            )
            if not contains_target:
                continue
            unit = _nearest_explicit_unit(lines, index)
            if unit in _AMOUNT_UNIT_SCALE and unit != "元":
                raise ValueError(
                    "filing target fact has explicit supported amount unit but no parseable value"
                )
        raise ValueError("filing has no target facts with locally proven CNY-yuan units")
    if "OPERATING_REVENUE" in facts and "NET_PROFIT_PARENT" in facts:
        revenue = facts["OPERATING_REVENUE"]
        if revenue != 0:
            facts["NET_PROFIT_MARGIN"] = facts["NET_PROFIT_PARENT"] / revenue
    return facts


def build_filing_fact_rows(
    *,
    entity_id: str,
    title: str,
    evidence_available_date: object,
    publication_timestamp: object,
    source_identity: str,
    provider: str,
    document_id: str,
    revision_id: str,
    document_url: str,
    document_sha256: str,
    text: str,
) -> pd.DataFrame:
    period_end = filing_period_end_from_title(title)
    facts = extract_standard_filing_facts(text)
    publication = pd.Timestamp(pd.to_datetime(publication_timestamp, errors="raise"))
    rows: list[dict[str, object]] = []
    for fact_type, value in sorted(facts.items()):
        unit = "RATIO" if fact_type == "NET_PROFIT_MARGIN" else (
            "CNY_PER_SHARE" if fact_type == "BASIC_EPS" else "CNY"
        )
        rows.append(
            {
                "entity_id": str(entity_id),
                "period_end": period_end,
                "fact_type": fact_type,
                "value": float(value),
                "unit": unit,
                "evidence_available_date": pd.Timestamp(evidence_available_date).normalize(),
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": FILING_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS))


_EXPLICIT_FILING_REVISION_MARKERS = (
    "修订",
    "更正",
    "修正",
    "更新",
)


def _explicit_filing_revision_priority(value: object) -> int:
    text = re.sub(r"\s+", "", str(value or ""))
    return int(any(marker in text for marker in _EXPLICIT_FILING_REVISION_MARKERS))


def latest_filing_fact_as_of(
    facts: pd.DataFrame,
    *,
    entity_id: str,
    fact_type: str,
    period_end: pd.Timestamp,
    as_of: pd.Timestamp,
) -> Mapping[str, object] | None:
    """Select the latest genuinely knowable filing version without hindsight.

    Ordering is first by market-available date and official publication
    timestamp. If multiple documents are published at the exact same timestamp,
    an explicitly titled revision/correction may supersede the original because
    that status is public at the same instant. Remaining ties are collapsed only
    when the standardized fact value and unit are identical; document/revision
    IDs are then used solely to choose a stable provenance representative, never
    to infer chronology. Conflicting unresolved ties remain fail-closed.
    """

    required = {
        "publication_timestamp",
        "evidence_available_date",
        "document_id",
        "revision_id",
        "value",
        "unit",
    }
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"filing fact revision ordering missing columns: {sorted(missing)}")
    rows = facts[
        facts["entity_id"].astype(str).eq(str(entity_id))
        & facts["fact_type"].astype(str).eq(fact_type)
        & pd.to_datetime(facts["period_end"]).dt.normalize().eq(period_end)
        & pd.to_datetime(facts["evidence_available_date"]).dt.normalize().le(as_of)
    ].copy()
    if rows.empty:
        return None
    rows["evidence_available_date"] = pd.to_datetime(
        rows["evidence_available_date"], errors="raise"
    ).dt.normalize()
    rows["publication_timestamp_order"] = pd.to_datetime(
        rows["publication_timestamp"], errors="raise", utc=True
    )
    latest_available = rows["evidence_available_date"].max()
    candidates = rows[rows["evidence_available_date"].eq(latest_available)].copy()
    latest_publication = candidates["publication_timestamp_order"].max()
    candidates = candidates[candidates["publication_timestamp_order"].eq(latest_publication)].copy()

    if len(candidates) != 1 and "filing_title" in candidates.columns:
        candidates["_explicit_revision_priority"] = candidates["filing_title"].map(
            _explicit_filing_revision_priority
        )
        max_priority = int(candidates["_explicit_revision_priority"].max())
        candidates = candidates[
            candidates["_explicit_revision_priority"].eq(max_priority)
        ].copy()

    if len(candidates) != 1 and "document_presentation_variant" in candidates.columns:
        variants = candidates["document_presentation_variant"].astype(str)
        known = {
            FILING_PRESENTATION_FULL,
            FILING_PRESENTATION_BODY,
            FILING_PRESENTATION_SUMMARY,
        }
        if bool(variants.isin(known).all()):
            priority = {
                FILING_PRESENTATION_FULL: 2,
                FILING_PRESENTATION_BODY: 1,
                FILING_PRESENTATION_SUMMARY: 0,
            }
            candidates["_presentation_priority"] = variants.map(priority)
            max_priority = int(candidates["_presentation_priority"].max())
            candidates = candidates[
                candidates["_presentation_priority"].eq(max_priority)
            ].copy()

    if len(candidates) != 1:
        semantic_signatures = {
            (float(row.value), str(row.unit))
            for row in candidates.itertuples()
        }
        if len(semantic_signatures) == 1:
            candidates = candidates.sort_values(
                ["document_id", "revision_id"], kind="stable"
            ).iloc[[0]].copy()
        else:
            identities = sorted(
                f"{row.document_id}/{row.revision_id}" for row in candidates.itertuples()
            )
            titles = sorted(
                {
                    f"{row.document_id}:{getattr(row, 'filing_title', '')}"
                    for row in candidates.itertuples()
                }
            )
            raise ValueError(
                "ambiguous same-availability filing revisions with conflicting facts: "
                f"entity={entity_id};period_end={pd.Timestamp(period_end).date()};"
                f"fact_type={fact_type};documents={','.join(identities)};"
                f"titles={' | '.join(titles)}"
            )

    return candidates.iloc[0].drop(
        labels=[
            "publication_timestamp_order",
            "_explicit_revision_priority",
            "_presentation_priority",
        ],
        errors="ignore",
    ).to_dict()


def derive_fundamental_trend_evidence(facts: pd.DataFrame) -> pd.DataFrame:
    """Derive only threshold-free, as-of trends from versioned filing facts."""

    required = {
        "entity_id",
        "period_end",
        "fact_type",
        "value",
        "unit",
        "evidence_available_date",
        "publication_timestamp",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "document_url",
        "document_sha256",
        "parser_version",
    }
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"filing facts missing columns: {sorted(missing)}")
    if facts.empty:
        return pd.DataFrame(columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))

    x = facts.copy()
    x["period_end"] = pd.to_datetime(x["period_end"], errors="raise").dt.normalize()
    x["evidence_available_date"] = pd.to_datetime(
        x["evidence_available_date"], errors="raise"
    ).dt.normalize()
    pd.to_datetime(x["publication_timestamp"], errors="raise", utc=True)
    if x.duplicated(["entity_id", "document_id", "revision_id", "fact_type"]).any():
        raise ValueError("filing facts contain duplicate document/fact identities")

    evidence_rows: list[dict[str, object]] = []
    evidence_types = {
        "OPERATING_REVENUE": "REVENUE_TREND",
        "NET_PROFIT_PARENT": "PROFIT_TREND",
        "OPERATING_CASH_FLOW_NET": "CASH_FLOW_TREND",
        "NET_PROFIT_MARGIN": "MARGIN_TREND",
    }
    for _, current in x[x["fact_type"].isin(evidence_types)].iterrows():
        period_end = pd.Timestamp(current["period_end"]).normalize()
        prior_end = period_end - pd.DateOffset(years=1)
        as_of = pd.Timestamp(current["evidence_available_date"]).normalize()
        prior = latest_filing_fact_as_of(
            x,
            entity_id=str(current["entity_id"]),
            fact_type=str(current["fact_type"]),
            period_end=prior_end,
            as_of=as_of,
        )
        if prior is None:
            continue
        current_value = float(current["value"])
        prior_value = float(prior["value"])
        if str(current["fact_type"]) == "NET_PROFIT_MARGIN":
            change = current_value - prior_value
            change_name = "yoy_delta"
        else:
            if prior_value == 0:
                continue
            change = current_value / prior_value - 1.0
            change_name = "yoy_change"

        payload = {
            "metric": str(current["fact_type"]),
            "current_value": current_value,
            "current_unit": str(current["unit"]),
            "prior_comparable_value": prior_value,
            "prior_period_end": str(pd.Timestamp(prior["period_end"]).date()),
            change_name: float(change),
            "formula_version": FILING_PARSER_VERSION,
            "current_document_id": str(current["document_id"]),
            "current_publication_timestamp": str(current["publication_timestamp"]),
            "current_document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_publication_timestamp": str(prior["publication_timestamp"]),
            "prior_document_sha256": str(prior["document_sha256"]),
        }
        provenance = {
            "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
            "provider": DERIVED_FUNDAMENTAL_PROVIDER,
            "derived_source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
            "source_filing_identity": str(current["source_identity"]),
            "source_provider": str(current["provider"]),
            "document_url": str(current["document_url"]),
            "document_id": str(current["document_id"]),
            "revision_id": str(current["revision_id"]),
            "publication_timestamp": str(current["publication_timestamp"]),
            "document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_revision_id": str(prior["revision_id"]),
            "prior_publication_timestamp": str(prior["publication_timestamp"]),
            "prior_document_sha256": str(prior["document_sha256"]),
            "as_of_selection": "LATEST_AVAILABLE_DATE_THEN_LATEST_OFFICIAL_PUBLICATION_TIMESTAMP",
            "later_restatements_do_not_rewrite_prior_evidence": True,
            "parser_version": FILING_PARSER_VERSION,
        }
        evidence_type = evidence_types[str(current["fact_type"])]
        observation_id = (
            f"{current['entity_id']}:{evidence_type}:{period_end.date()}:"
            f"{current['document_id']}:{current['revision_id']}"
        )
        evidence_rows.append(
            {
                "evidence_id": f"derived-fundamental:{_stable_hash(observation_id)}",
                "entity_id": str(current["entity_id"]),
                "evidence_type": evidence_type,
                "event_date": period_end,
                "evidence_available_date": as_of,
                "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                "provider": DERIVED_FUNDAMENTAL_PROVIDER,
                "document_id": str(current["document_id"]),
                "revision_id": str(current["revision_id"]),
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(
                    {
                        "observation": observation_id,
                        "payload": payload,
                        "available": str(as_of.date()),
                    }
                ),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "source_url_identity": str(current["document_url"]),
            }
        )
    if not evidence_rows:
        return pd.DataFrame(columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))
    return validate_materialized_pit_records(
        pd.DataFrame(evidence_rows, columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))
    )


__all__ = [
    "DERIVED_FUNDAMENTAL_SOURCE_ID",
    "DERIVED_FUNDAMENTAL_PROVIDER",
    "FILING_PARSER_VERSION",
    "FILING_FACT_COLUMNS",
    "DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS",
    "DownloadedOfficialDocument",
    "download_official_document",
    "extract_pdf_text",
    "filing_period_end_from_title",
    "extract_standard_filing_facts",
    "build_filing_fact_rows",
    "latest_filing_fact_as_of",
    "derive_fundamental_trend_evidence",
]
