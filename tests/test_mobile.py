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
