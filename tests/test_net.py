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
    handlers = [h for h in net._opener().handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert [h._context for h in handlers] == [net.tls_context()]
    seen = []

    class Answer:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b""

        def close(self):
            pass

    def fake(url, timeout=None):
        seen.append(url)
        return Answer()

    monkeypatch.setattr(net, "_open", fake)
    assert build_city.download("https://example.invalid/a.zip", tmp_path / "a.zip")
    assert len(seen) == 1


def cert_error(code: int, message: str) -> urllib.error.URLError:
    e = ssl.SSLCertVerificationError(1, "certificate verify failed")
    e.verify_code, e.verify_message = code, message
    return urllib.error.URLError(e)


def test_a_missing_root_names_the_fix(monkeypatch, tmp_path, capsys):
    def fail(url, timeout=None):
        raise cert_error(20, "unable to get local issuer certificate")

    monkeypatch.setattr(net, "_open", fail)
    assert build_city.download("https://example.invalid/a.zip", tmp_path / "a.zip") is False
    assert "point SSL_CERT_FILE at it, or download the file by hand" in capsys.readouterr().out


def test_an_expired_or_wrong_host_certificate_is_only_reported():
    for code, message in ((10, "certificate has expired"), (62, "hostname mismatch")):
        note = net.tls_note(cert_error(code, message))
        assert note == f" (certificate rejected: {message})" and "SSL_CERT_FILE" not in note
    assert net.tls_note(urllib.error.URLError("timed out")) == ""


@pytest.mark.parametrize("to, followed", [
    ("https://mirror.example/a.zip", True),
    ("http://mirror.example/a.zip", False),  # a downgrade: anyone on the path could change the file
    ("ftp://mirror.example/a.zip", False),
    ("HTTP://mirror.example/a.zip", False),
])
def test_a_redirect_is_followed_to_https_only(to, followed):
    handler = next(h for h in net._opener().handlers if isinstance(h, net._HttpsRedirectsOnly))
    req = urllib.request.Request("https://example.invalid/a.zip")
    if followed:
        assert handler.redirect_request(req, None, 302, "Found", {}, to).full_url == to
    else:
        with pytest.raises(urllib.error.HTTPError, match="non-https"):
            handler.redirect_request(req, None, 302, "Found", {}, to)


def test_a_download_stops_at_its_deadline(monkeypatch):
    import io
    import types
    clock = iter(range(0, 1000, 10))  # every reading of the clock is 10 s later
    monkeypatch.setattr(net, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))  # net's clock only

    class Slow(io.BytesIO):
        pass
    monkeypatch.setattr(net, "_open", lambda url, timeout: Slow(b"x" * (net.READ_PIECE * 10)))
    with net.urlopen("https://example.invalid/a", timeout=5, deadline=35) as r, pytest.raises(TimeoutError, match="35 s"):
        r.read()
    with net.urlopen("https://example.invalid/a", timeout=5, deadline=None) as r:  # no deadline: the whole body
        assert len(r.read()) == net.READ_PIECE * 10
