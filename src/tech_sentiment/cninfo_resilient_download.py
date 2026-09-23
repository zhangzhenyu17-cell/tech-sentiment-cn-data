from __future__ import annotations

from hashlib import sha256
from urllib.error import HTTPError

from requests.exceptions import ChunkedEncodingError

from .bounded_retry import is_transient_network_error
from .official_filing_facts import (
    DownloadedOfficialDocument,
    _cninfo_https_download_fallback,
    _decode_official_transport_body,
    _download_cninfo_with_browser_transport,
    _download_cninfo_with_https_session,
    _looks_like_html,
    _looks_like_pdf,
    _validate_same_provider_retrieval,
    download_official_document,
)


def _session_failure_allows_browser_fallback(exc: BaseException) -> bool:
    """Return True only for safe CNINFO session-transport recovery cases.

    requests wraps an incomplete HTTP entity body as ChunkedEncodingError even
    when the underlying cause is http.client.IncompleteRead.  That is the same
    transport-truncation class that already permits recovery after urllib
    exhaustion, so the existing same-provider browser transport may be tried.
    Semantic/content-validation errors remain fail-closed.
    """

    return (
        isinstance(exc, ChunkedEncodingError)
        or (isinstance(exc, HTTPError) and exc.code == 403)
        or is_transient_network_error(exc)
    )


def download_cninfo_document_resilient(
    url: str,
    *,
    timeout: float = 30.0,
) -> DownloadedOfficialDocument:
    """Recover one exact CNINFO PDF after bounded urllib transport exhaustion.

    The canonical immutable attachment remains authoritative. This adapter is
    entered only when the existing official downloader exhausts a transport
    failure already classified as transient. Recovery then uses only the
    existing same-provider HTTPS session/browser transports; no source search,
    document substitution, HTTP downgrade, or evidence-rule change is allowed.
    """

    try:
        return download_official_document(url, timeout=timeout)
    except Exception as exc:
        fallback_url = _cninfo_https_download_fallback(url)
        if fallback_url is None or not is_transient_network_error(exc):
            raise

    transport_method = "cninfo_same_provider_session"
    try:
        content, retrieval_url = _download_cninfo_with_https_session(
            url,
            fallback_url,
            timeout=timeout,
        )
    except Exception as session_exc:
        if not _session_failure_allows_browser_fallback(session_exc):
            raise
        content, retrieval_url = _download_cninfo_with_browser_transport(
            url,
            fallback_url,
            timeout=timeout,
        )
        transport_method = "cninfo_same_provider_browser"

    _validate_same_provider_retrieval(url, retrieval_url)
    if not content:
        raise ValueError("official filing attachment is empty")

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


__all__ = ["download_cninfo_document_resilient"]
