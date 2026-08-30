#!/usr/bin/env python3
"""serve.py — Read-only HTTP aggregator for agent-board.

Python 3 stdlib only. Serves the frozen Unified Core JSON Contract:
    GET /                     -> index.html
    GET /api/board?board=X    -> aggregated board JSON
    GET /api/boards           -> list of boards
    GET /api/team?team=X      -> alias for /api/board
    GET /api/teams            -> alias for /api/boards
    anything else             -> 404; non-GET -> 405
"""

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Ensure package root is in sys.path so 'board' can be imported when
# serve.py is run directly
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

try:
    from board.adapters import get_adapter
    from board.adapters.base import clean_text, read_text, valid_name
except (ImportError, ValueError):
    from adapters import get_adapter
    from adapters.base import clean_text, read_text, valid_name

BOARD_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_INDEX = os.path.join(BOARD_DIR, "templates", "index.html")
DIRECT_INDEX = os.path.join(BOARD_DIR, "index.html")


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "agent-board/1.0"
    adapter = None

    def log_message(self, fmt, *args):
        # Keep console quiet in normal operation
        pass

    def _send(self, code: int, body: Any, content_type: str):
        payload = body if isinstance(
            body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, code: int, obj: Any):
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False),
            "application/json; charset=utf-8",
        )

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            # Read index.html dynamically
            html = read_text(TEMPLATES_INDEX, limit=1 << 22)
            if html is None:
                html = read_text(DIRECT_INDEX, limit=1 << 22)
            if html is None:
                self._send(
                    503,
                    "index.html is not available\n",
                    "text/plain; charset=utf-8",
                )
                return
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path in ("/api/boards", "/api/teams"):
            boards = self.adapter.list_boards()
            # Return dual keys for compatibility
            self._json(200, {"boards": boards, "teams": boards})
            return

        if path in ("/api/board", "/api/team"):
            boards = self.adapter.list_boards()
            names = [b["name"] for b in boards]

            requested = query.get(
                "board", [None])[0] or query.get(
                "team", [None])[0]
            if requested is None:
                if len(names) == 1:
                    requested = names[0]
                elif len(names) > 1:
                    self._json(400, {"error": "board_required",
                               "boards": names, "teams": names})
                    return
                else:
                    requested = "default"

            # Check safe identifier
            if not valid_name(requested):
                self._json(400, {"error": "board_required",
                           "boards": names, "teams": names})
                return

            if names and requested not in names:
                self._json(400, {"error": "board_required",
                           "boards": names, "teams": names})
                return

            try:
                payload = self.adapter.load_board(requested)
                self._json(200, payload)
            except Exception as e:
                self._json(500, {"error": "internal_error", "message": str(e)})
            return

        self._json(404, {"error": "not_found"})

    def do_HEAD(self):
        self.do_GET()

    def _reject(self):
        self._json(
            405,
            {
                "error": "method_not_allowed",
                "message": "agent-board is strictly read-only",
            },
        )

    do_POST = _reject
    do_PUT = _reject
    do_PATCH = _reject
    do_DELETE = _reject
    do_OPTIONS = _reject


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="agent-board HTTP server (read-only)")
    parser.add_argument(
        "--root",
        "--board-root",
        "--teams-root",
        dest="root_dir",
        default=".",
        help="path to data root directory")
    parser.add_argument(
        "--adapter",
        choices=[
            "auto",
            "native",
            "teams",
            "tmux-agent-teams"],
        default="auto")
    parser.add_argument(
        "--port",
        type=int,
        default=8737,
        help="0 picks a free port")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="bind address; defaults to 0.0.0.0 (reachable from the LAN, "
             "no auth — pass --host 127.0.0.1 to keep it local-only)")
    args = parser.parse_args(argv)

    root_dir = os.path.abspath(args.root_dir)
    if not os.path.isdir(root_dir):
        print(f"data root is not a directory: {root_dir}", file=sys.stderr)
        return 1

    adapter = get_adapter(root_dir, args.adapter)
    handler = type("BoundBoardHandler", (BoardHandler,), {"adapter": adapter})
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    httpd.daemon_threads = True

    assigned_port = httpd.server_address[1]
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "agent-board: bound to a non-loopback address — reachable from "
            "anyone on the LAN, with no authentication. Task titles, "
            "worker names, and contract text are readable by anyone who "
            "can reach this port. Pass --host 127.0.0.1 to keep it "
            "local-only.",
            file=sys.stderr,
        )
    print(
        f"agent-board listening on http://{
            args.host}:{assigned_port} (root={root_dir}, adapter={
            adapter.__class__.__name__})",
        flush=True,
    )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
