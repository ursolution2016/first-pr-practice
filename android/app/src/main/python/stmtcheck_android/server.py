"""Starts the Statement Check web app inside the Android app, on 127.0.0.1 only.

Other apps on a phone can connect to localhost, so the server (a) listens on a random port, (b) requires a
login session, and (c) hands the session only to the app's own WebView through a per-launch secret URL
(/_bootstrap/<token>). The access code is random and never shown, so the login form is unusable.
"""
import os
import secrets
import threading

_state = {}


def start(files_dir, cache_dir):
    """Start (once) and return [port, bootstrap_token]."""
    if _state:
        return [_state["port"], _state["token"]]
    home = os.path.join(files_dir, "library")
    inbox = os.path.join(cache_dir, "inbox")
    os.makedirs(home, exist_ok=True)
    os.makedirs(inbox, exist_ok=True)
    os.environ["STMTCHECK_HOME"] = home
    os.environ["STMTCHECK_INBOX"] = inbox

    from werkzeug.serving import make_server
    from stmtcheck.web import create_app

    token = secrets.token_hex(16)
    app = create_app(home, access_code=secrets.token_hex(16), bootstrap_token=token)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, name="stmtcheck-server", daemon=True).start()
    _state.update(port=server.server_port, token=token, server=server)
    return [_state["port"], token]
