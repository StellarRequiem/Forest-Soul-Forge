"""Tiny no-cache static server for the dev frontend.

Wraps the stdlib SimpleHTTPRequestHandler to send `Cache-Control: no-store`
on every response. Without this, browsers (especially Safari) hold onto
``index.html`` and the JS modules across daemon restarts — when the user
returns after a deploy they see a stale UI that's missing tabs, errors
on routes that no longer exist, etc.

The asset URLs in `index.html` also carry a ``?v=...`` query string for
belt-and-suspenders cache busting (demo-friction audit 2026-04-28 P0 #2).
This server enforces freshness for everything, including ES-module
children that the version-stamp can't reach.

Usage::

    python -m frontend.serve            # default port 5173, bind 127.0.0.1
    python -m frontend.serve 5174       # override port
    python frontend/serve.py 5173 0.0.0.0   # override bind too
"""
from __future__ import annotations

import http.server
import socketserver
import sys
from pathlib import Path

DEFAULT_PORT = 5173
DEFAULT_BIND = "127.0.0.1"


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Adds Cache-Control: no-store to every response.

    Subclasses ``end_headers`` (called once per response, just before the
    body) so every method — GET / HEAD / OPTIONS — inherits the header
    without us having to override each one.
    """

    def end_headers(self) -> None:
        # Defense in depth — three headers cover every browser + intermediate
        # cache combination we're likely to see in dev.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — match parent
        # Quieter than the default — drop the source-IP prefix the parent prints.
        sys.stderr.write("[frontend] " + (format % args) + "\n")


def serve(port: int = DEFAULT_PORT, bind: str = DEFAULT_BIND) -> None:
    """Start the no-cache server in the current working directory.

    The caller is expected to ``cd frontend`` before invoking — same
    contract as ``python -m http.server`` so existing scripts can swap
    in this module without changing the cwd they run from.
    """
    here = Path.cwd()
    if not (here / "index.html").exists():
        sys.stderr.write(
            f"[frontend] No index.html in {here}. "
            f"Did you forget to `cd frontend` before running?\n"
        )
        sys.exit(2)

    # ThreadingHTTPServer matches the stdlib `python -m http.server`
    # behavior so existing kill_port logic in run.command keeps working.
    socketserver.TCPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer((bind, port), NoCacheHandler) as httpd:
        sys.stderr.write(f"[frontend] Serving {here} at http://{bind}:{port}/ (no-cache)\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\n[frontend] Stopping.\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    bind = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BIND
    serve(port=port, bind=bind)
