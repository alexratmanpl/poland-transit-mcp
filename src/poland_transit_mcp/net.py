"""Outbound HTTPS for downloads (feeds, map extracts, city files, the opt-in live feed).

Python's default trust store depends on how Python was installed. The standalone builds that uv installs on macOS
read /etc/ssl/cert.pem, which lacks some roots that publishers use (for example the Certum root behind Wrocław's
and Poznań's open-data servers), so those downloads failed with CERTIFICATE_VERIFY_FAILED. Every download therefore
trusts the system's roots (as Python finds them, including SSL_CERT_FILE / SSL_CERT_DIR and, on Windows, the
system store) **plus** Mozilla's root list from `certifi`. Nothing is removed, so a company proxy's root added to
the system keeps working. The context is built on the first download; set SSL_CERT_FILE before starting.
"""
from __future__ import annotations

import ssl
import urllib.request
from functools import lru_cache

import certifi

# verify codes where a missing root is the likely cause (OpenSSL X509_V_ERR_*): unable to get issuer certificate (2),
# self-signed certificate in chain (19), unable to get local issuer certificate (20)
MISSING_ROOT = {2, 19, 20}
TLS_HINT = ("the server's root certificate is not trusted here; if you trust another root store, point "
            "SSL_CERT_FILE at it, or download the file by hand")


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


def urlopen(url: str | urllib.request.Request, timeout: float):
    """urllib.request.urlopen with the TLS context above."""
    return urllib.request.urlopen(url, timeout=timeout, context=tls_context())  # noqa: S310 (https sources from config)


def _cert_error(e: BaseException) -> ssl.SSLCertVerificationError | None:
    for x in (e, getattr(e, "reason", None)):
        if isinstance(x, ssl.SSLCertVerificationError):
            return x
    return None


def is_tls_failure(e: BaseException) -> bool:
    return _cert_error(e) is not None


def tls_note(e: BaseException) -> str:
    """A short note to add to a failed download's message: how to fix a missing root, or what was rejected (an
    expired or wrong-host certificate is not worked around)."""
    err = _cert_error(e)
    if err is None:
        return ""
    if err.verify_code in MISSING_ROOT:
        return f" ({TLS_HINT})"
    return f" (certificate rejected: {err.verify_message})"
