"""WBJ Terminal as a native desktop app.

Starts the local web server in a background thread and opens the terminal in
a native OS window (pywebview → WebView2 on Windows) — no browser chrome, no
tabs, its own taskbar icon. Close the window to quit.

Usage:  .venv\\Scripts\\python.exe scripts\\desktop.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

# Allow running as a loose script (scripts/ isn't a package).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import webview  # noqa: E402  (import after sys.path tweak)

import webapp  # noqa: E402  (scripts/webapp.py — the server + handlers)

URL = f"http://127.0.0.1:{webapp.PORT}/terminal"


def _server_already_up() -> bool:
    """True if something is already serving on the terminal's port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", webapp.PORT)) == 0


def _serve() -> None:
    httpd = webapp.ThreadingHTTPServer(("127.0.0.1", webapp.PORT), webapp.Handler)
    httpd.serve_forever()


def main() -> None:
    if not _server_already_up():
        threading.Thread(target=_serve, daemon=True).start()
        # give the server a moment to bind before the window loads the URL
        for _ in range(50):
            if _server_already_up():
                break
            time.sleep(0.1)

    webview.create_window(
        "WBJ Terminal",
        URL,
        width=1440,
        height=900,
        min_size=(900, 600),
        background_color="#0a0e14",
    )
    webview.start()  # blocks until the window is closed


if __name__ == "__main__":
    main()
