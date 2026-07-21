"""WBJ Terminal as a native desktop app.

Starts the local web server in a background thread and opens the terminal in a
native OS window (pywebview -> WebView2 on Windows). If the native window can't
be created for ANY reason, it falls back to opening the terminal in the default
browser, so something ALWAYS shows. Errors are written to desktop_error.log.

Usage:  .venv\\Scripts\\pythonw.exe scripts\\desktop.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

# Allow running as a loose script (scripts/ isn't a package).
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

_LOG = _HERE / "desktop_error.log"


def _log(msg: str) -> None:
    """Append a line to the error log (never raises — pythonw has no console)."""
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# Point WebView2's user-data/cache folder at a LOCAL (non-OneDrive) path, so
# OneDrive file-sync/locking can't block the window from initializing — a common
# silent failure when the app lives under OneDrive.
try:
    _wv2 = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WBJTerminal" / "WebView2"
    _wv2.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(_wv2))
except Exception:
    pass

import webapp  # noqa: E402  (scripts/webapp.py — the server + handlers)

# 127.0.0.1 literal, NOT "localhost": Windows browsers/WebView resolve localhost
# to ::1 (IPv6) first, where the IPv4-bound server isn't listening.
URL = f"http://127.0.0.1:{webapp.PORT}/terminal"

try:
    import webview  # noqa: E402
    _HAS_WEBVIEW = True
except Exception:
    _HAS_WEBVIEW = False


def _server_already_up() -> bool:
    """True if something is already serving on the terminal's port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", webapp.PORT)) == 0


def _serve() -> None:
    webapp.ThreadingHTTPServer(("127.0.0.1", webapp.PORT), webapp.Handler).serve_forever()


def _open_browser_and_wait() -> None:
    """Fallback: open the terminal in the default browser, keep the server alive."""
    try:
        webbrowser.open(URL)
    except Exception:
        _log(traceback.format_exc())
    try:
        while True:
            time.sleep(3600)  # keep the process (and its daemon server) alive
    except KeyboardInterrupt:
        pass


def main() -> None:
    # 1) Ensure the local server is up (start it if nobody else is serving).
    if not _server_already_up():
        threading.Thread(target=_serve, daemon=True).start()
        for _ in range(80):
            if _server_already_up():
                break
            time.sleep(0.1)

    # 2) Native window, with an automatic browser fallback on any failure.
    if not _HAS_WEBVIEW:
        _log("pywebview no disponible -> fallback al navegador")
        _open_browser_and_wait()
        return
    try:
        webview.create_window(
            "WBJ Terminal", URL,
            width=1440, height=900, min_size=(900, 600),
            background_color="#0a0e14",
        )
        webview.start()  # blocks until the window is closed
    except Exception:
        _log("=== fallo la ventana nativa (WebView2) — abriendo en el navegador ===")
        _log(traceback.format_exc())
        _open_browser_and_wait()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log("=== error fatal en desktop.py ===")
        _log(traceback.format_exc())
        _open_browser_and_wait()
