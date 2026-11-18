"""Downloads trust the system's roots plus Mozilla's (net.py); a certificate failure says what to do."""
import ssl
import urllib.error
import urllib.request

import certifi
import pytest

from poland_transit_mcp import net
from poland_transit_mcp.build import build_city


@pytest.fixture(autouse=True)
def fresh_context():
    net.tls_context.cache_clear()
    yield
    net.tls_context.cache_clear()


def test_system_roots_plus_certifi(monkeypatch):
    calls = []
    real_default, real_load = ssl.create_default_context, ssl.SSLContext.load_verify_locations

    def default(*a, **k):
        calls.append(("default", a, k))
        return real_default(*a, **k)

    def load(self, *a, **k):
        calls.append(("load", a, k))
        return real_load(self, *a, **k)

    monkeypatch.setattr(ssl, "create_default_context", default)
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", load)
    ctx = net.tls_context()
    assert calls[0] == ("default", (), {})  # the system's roots, as Python finds them (SSL_CERT_FILE included)
    assert ("load", (), {"cafile": certifi.where()}) in calls  # plus Mozilla's
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_every_download_passes_the_context(monkeypatch, tmp_path):
    seen = []

    class Answer:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b""

    def fake(url, timeout=None, context=None):
        seen.append(context)
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert build_city.download("https://example.invalid/a.zip", tmp_path / "a.zip")
    assert seen == [net.tls_context()]


def cert_error(code: int, message: str) -> urllib.error.URLError:
    e = ssl.SSLCertVerificationError(1, "certificate verify failed")
    e.verify_code, e.verify_message = code, message
    return urllib.error.URLError(e)


def test_a_missing_root_names_the_fix(monkeypatch, tmp_path, capsys):
    def fail(url, timeout=None, context=None):
        raise cert_error(20, "unable to get local issuer certificate")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert build_city.download("https://example.invalid/a.zip", tmp_path / "a.zip") is False
    assert "point SSL_CERT_FILE at it, or download the file by hand" in capsys.readouterr().out


def test_an_expired_or_wrong_host_certificate_is_only_reported():
    for code, message in ((10, "certificate has expired"), (62, "hostname mismatch")):
        note = net.tls_note(cert_error(code, message))
        assert note == f" (certificate rejected: {message})" and "SSL_CERT_FILE" not in note
    assert net.tls_note(urllib.error.URLError("timed out")) == ""
