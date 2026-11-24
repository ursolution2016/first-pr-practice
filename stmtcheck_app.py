"""Entry point for the packaged executable.

Double-click (no arguments): start the local web app and open it in the browser.
With arguments it behaves exactly like `python -m stmtcheck` (check / library / learn / serve).
"""
import multiprocessing
import socket
import sys
import threading
import webbrowser


def _free_port(start: int = 8000) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from stmtcheck.cli import main

    argv = sys.argv[1:]
    if not argv:
        port = _free_port()
        print(f"Statement Check is running at http://127.0.0.1:{port}  (close this window to stop)")
        threading.Timer(1.5, webbrowser.open, args=(f"http://127.0.0.1:{port}/",)).start()
        argv = ["serve", "--port", str(port)]
    sys.exit(main(argv))
