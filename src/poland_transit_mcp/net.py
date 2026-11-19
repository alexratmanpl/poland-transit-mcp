"""Outbound HTTPS for downloads (feeds, map extracts, city files, the opt-in live feed).

Python's default trust store depends on how Python was installed. The standalone builds that uv installs on macOS
read /etc/ssl/cert.pem, which lacks some roots that publishers use (for example the Certum root behind Wrocław's
and Poznań's open-data servers), so those downloads failed with CERTIFICATE_VERIFY_FAILED. Every download therefore
trusts the system's roots (as Python finds them, including SSL_CERT_FILE / SSL_CERT_DIR and, on Windows, the
system store) **plus** Mozilla's root list from `certifi`. Nothing is removed, so a company proxy's root added to
the system keeps working. The context is built on the first download; set SSL_CERT_FILE before starting.

A redirect is followed only to another https address: urllib would follow one to plain http or ftp, where anyone on
the path can change the file. `timeout` limits each wait for the network; `deadline` limits a whole download, so a
server that sends a byte now and then cannot hold it for ever.
"""
from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.parse
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


DEFAULT_DEADLINE_S = 3600.0  # a whole download, unless the caller sets its own
READ_PIECE = 64 << 10  # the clock is checked between pieces of at most this many bytes


class _HttpsRedirectsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(req.full_url, code, f"redirect to a non-https address refused: "
                                         f"{urllib.parse.urlsplit(newurl).scheme or '?'}://...", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls_context()), _HttpsRedirectsOnly())


def _open(url: str | urllib.request.Request, timeout: float):
    return _opener().open(url, timeout=timeout)


class _Deadline:
    """A response whose reads stop with TimeoutError once the whole download has taken longer than its deadline."""

    def __init__(self, response, deadline_s: float, started: float):
        self._r, self._end, self._deadline_s = response, started + deadline_s, deadline_s

    def _check(self) -> None:
        if time.monotonic() > self._end:
            raise TimeoutError(f"the download took longer than {self._deadline_s:g} s")

    def read(self, n: int | None = -1) -> bytes:
        if n is not None and n >= 0:
            out = bytearray()
            while len(out) < n:
                self._check()
                piece = self._r.read(min(READ_PIECE, n - len(out)))
                if not piece:
                    break
                out += piece
            return bytes(out)
        pieces = []
        while True:
            self._check()
            piece = self._r.read(READ_PIECE)
            if not piece:
                return b"".join(pieces)
            pieces.append(piece)

    def __getattr__(self, name):  # headers, status, url, close, ...
        return getattr(self._r, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._r.close()
        return False


def urlopen(url: str | urllib.request.Request, timeout: float, deadline: float | None = DEFAULT_DEADLINE_S):
    """Opens `url` with the TLS context above, following redirects to https only; `timeout` seconds for each wait,
    `deadline` seconds for the whole download (None: no limit)."""
    started = time.monotonic()
    r = _open(url, timeout)
    return r if deadline is None else _Deadline(r, deadline, started)


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
