"""The daemon: a thin HTTP shell around Merchant.handle_safe().

All decisions live in core.Merchant; this module only speaks HTTP, writes
the pidfile, and exits cleanly on SIGTERM. Run via `pserv start` (Manager
forks it) or directly:  python -m scutl_pserv.server
"""

from __future__ import annotations

import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import Merchant
from .state import Decommissioned, StateDir


def main() -> None:
    state = StateDir()
    state.check_not_decommissioned()
    merchant = Merchant(state)
    config = merchant.config

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                resp = merchant.handle_safe(self.path, self.headers.get("X-PAYMENT"))
            except Decommissioned:
                resp = None
            if resp is None:
                self.send_response(410)
                self.end_headers()
                return
            self.send_response(resp.code)
            self.send_header("content-type", resp.content_type)
            self.send_header("content-length", str(len(resp.body)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.body)

        def log_message(self, fmt, *args):
            print(f"[pserv] {fmt % args}", file=sys.stderr)

    server = ThreadingHTTPServer((config["bind_addr"], int(config["bind_port"])), Handler)
    state.pidfile.write_text(str(os.getpid()))

    def shutdown(signum, frame):
        state.pidfile.unlink(missing_ok=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        state.pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
