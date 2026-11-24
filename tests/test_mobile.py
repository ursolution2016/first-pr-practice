"""Mobile/LAN access: access code, rate limiting, manifest and icons."""
import io

import pytest
from PIL import Image

from stmtcheck.cli import main
from stmtcheck.web import create_app, serve
from test_stmtcheck import build_pdf


@pytest.fixture()
def locked(tmp_path):
    app = create_app(library_root=tmp_path / "lib", access_code="abc23xyz")
    c = app.test_client()
    c.csrf = app.csrf_token
    return c


def test_everything_requires_the_code(locked):
    for path in ("/", "/library", "/about"):
        r = locked.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    assert locked.post("/library/add", data={}).status_code == 302
    r = locked.post("/api/analyze", data={})
    assert r.status_code == 401


def test_login_flow(locked):
    assert locked.get("/login").status_code == 200
    bad = locked.post("/login", data={"code": "nope"})
    assert bad.status_code == 401 and locked.get("/").status_code == 302
    ok = locked.post("/login", data={"code": "abc23xyz"})
    assert ok.status_code == 302
    cookie = ok.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert locked.get("/").status_code == 200


def test_api_accepts_header_code(locked, tmp_path):
    build_pdf(tmp_path / "s.pdf")
    r = locked.post("/api/analyze", headers={"X-Access-Code": "abc23xyz"},
                    data={"file": (io.BytesIO((tmp_path / "s.pdf").read_bytes()), "s.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and "verdict" in r.get_json()
    assert locked.post("/api/analyze", headers={"X-Access-Code": "wrong"}).status_code == 401


def test_failed_attempts_are_rate_limited(locked):
    for _ in range(5):
        assert locked.post("/login", data={"code": "bad"}).status_code == 401
    # even the right code is refused during the lockout
    assert locked.post("/login", data={"code": "abc23xyz"}).status_code == 429


def test_manifest_and_icons_are_public_and_valid(locked):
    m = locked.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.get_json()["display"] == "standalone"
    for size in (192, 512):
        r = locked.get(f"/icon-{size}.png")
        assert r.status_code == 200 and Image.open(io.BytesIO(r.data)).size == (size, size)
    assert locked.get("/icon-64.png").status_code == 302  # unknown paths stay behind the login
    locked.post("/login", data={"code": "abc23xyz"})
    assert locked.get("/icon-64.png").status_code == 404


def test_pages_declare_mobile_metadata(tmp_path):
    c = create_app(library_root=tmp_path / "l").test_client()
    html = c.get("/").data.decode()
    assert "viewport" in html and 'rel="manifest"' in html and "theme-color" in html
    assert c.get("/login").status_code == 302  # no code configured: login not needed


def test_no_code_still_open_on_localhost(tmp_path):
    assert create_app(library_root=tmp_path / "l").test_client().get("/library").status_code == 200


def test_listening_beyond_localhost_requires_a_code(tmp_path):
    with pytest.raises(ValueError, match="access code"):
        serve("0.0.0.0", 0, tmp_path / "l", None)


# ------------------------------------------------- cloud / Android support
def test_export_works_over_get_for_download_managers(tmp_path):
    import re
    from decimal import Decimal
    app = create_app(library_root=tmp_path / "l", access_code="abc23xyz")
    c = app.test_client()
    c.post("/login", data={"code": "abc23xyz"})
    build_pdf(tmp_path / "t.pdf", balance_override={2: Decimal("2000.00")})
    res = c.post("/analyze", data={"csrf": app.csrf_token, "against": "none",
                                   "file": (io.BytesIO((tmp_path / "t.pdf").read_bytes()), "t.pdf")},
                 content_type="multipart/form-data")
    html = res.data.decode()
    assert 'method="get" action="/export/' in html
    token = re.search(r'action="/export/([^"]+)"', html)[1]
    r = c.get(f"/export/{token}?fmt=csv")
    assert r.status_code == 200 and r.mimetype == "text/csv" and b"UNVERIFIED" in r.data
    assert "attachment" in r.headers["Content-Disposition"]
    assert c.get(f"/export/{token}?fmt=pdf").data.startswith(b"%PDF")
    # a different, logged-out client cannot fetch it
    assert create_app(library_root=tmp_path / "l2", access_code="abc23xyz").test_client() \
        .get(f"/export/{token}?fmt=csv").status_code == 302


def test_security_headers_and_no_store(tmp_path):
    r = create_app(library_root=tmp_path / "l").test_client().get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Referrer-Policy"] == "no-referrer"


def test_cookie_is_secure_behind_a_tls_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("STMTCHECK_TRUST_PROXY", "1")
    app = create_app(library_root=tmp_path / "l", access_code="a-long-code-123")
    r = app.test_client().post("/login", data={"code": "a-long-code-123"},
                               headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"})
    assert "Secure" in r.headers["Set-Cookie"]


def test_proxy_forwarded_ip_drives_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("STMTCHECK_TRUST_PROXY", "1")
    c = create_app(library_root=tmp_path / "l", access_code="a-long-code-123").test_client()
    h = lambda ip: {"X-Forwarded-For": ip}
    for _ in range(5):
        c.post("/login", data={"code": "x"}, headers=h("203.0.113.9"))
    assert c.post("/login", data={"code": "a-long-code-123"}, headers=h("203.0.113.9")).status_code == 429
    assert c.post("/login", data={"code": "a-long-code-123"}, headers=h("198.51.100.1")).status_code == 302


def test_server_mode_demands_a_strong_code(tmp_path, monkeypatch):
    monkeypatch.setenv("STMTCHECK_TRUST_PROXY", "1")
    monkeypatch.delenv("STMTCHECK_ACCESS_CODE", raising=False)
    with pytest.raises(ValueError, match="at least 12"):
        serve("0.0.0.0", 0, tmp_path / "l", "short")


def test_waitress_trusts_one_proxy_hop(tmp_path, monkeypatch):
    """Regression: waitress drops X-Forwarded-* by default, which broke Secure cookies and
    made every user share one rate-limit bucket behind the proxy."""
    import socket
    import threading
    import urllib.error
    import urllib.request

    waitress = pytest.importorskip("waitress")
    monkeypatch.setenv("STMTCHECK_TRUST_PROXY", "1")
    app = create_app(library_root=tmp_path / "l", access_code="a-long-code-123")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = waitress.create_server(app, host="127.0.0.1", port=port, trusted_proxy="*",
                                    trusted_proxy_count=1,
                                    trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto",
                                                           "x-forwarded-host"})
    threading.Thread(target=server.run, daemon=True).start()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def login(code, ip):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/login", data=f"code={code}".encode(),
                                     headers={"X-Forwarded-For": ip, "X-Forwarded-Proto": "https"})
        try:
            return urllib.request.build_opener(NoRedirect).open(req)
        except urllib.error.HTTPError as e:
            return e

    try:
        ok = login("a-long-code-123", "203.0.113.5")
        assert ok.code == 302 and "Secure" in ok.headers["Set-Cookie"]
        for _ in range(5):
            login("bad", "198.51.100.7")
        assert login("a-long-code-123", "198.51.100.7").code == 429
        assert login("a-long-code-123", "198.51.100.99").code == 302  # other clients unaffected
    finally:
        server.close()
