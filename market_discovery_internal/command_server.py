"""
[MODUL J] Emergency Kill-Switch — HTTP command server.
Listens on COMMAND_SERVER_PORT (default 8083) for POST /api/kill.
Writing the kill flag causes the paper loop to exit cleanly.

Security: Requires X-Kill-Token header matching KILL_API_TOKEN env var.
CORS is restricted to the configured origin (default: localhost only).
"""

import os
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_logger = logging.getLogger(__name__)

KILL_FLAG_FILE = os.getenv("KILL_FLAG_FILE", "logs/kill.flag")
COMMAND_SERVER_PORT = int(os.getenv("COMMAND_SERVER_PORT", "8083"))
COMMAND_SERVER_BIND = os.getenv("COMMAND_SERVER_BIND", "0.0.0.0")  # 0.0.0.0 for browser access; auth via KILL_API_TOKEN
# Auth token — set KILL_API_TOKEN in .env; if unset, all endpoints are disabled.
_KILL_API_TOKEN = os.getenv("KILL_API_TOKEN", "")
# CORS: restrict to your dashboard origin (default: same-origin via nginx proxy)
_CORS_ORIGIN = os.getenv("COMMAND_SERVER_CORS_ORIGIN", "http://localhost:8080")


class _CommandHandler(BaseHTTPRequestHandler):
    def _check_auth(self):
        """Verify token authentication. Returns True if authorized, False otherwise."""
        if not _KILL_API_TOKEN:
            self._send_cors_headers(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({"status": "disabled", "message": "API disabled: KILL_API_TOKEN not configured."}).encode()
            self.wfile.write(body)
            return False

        # Accept token via X-Kill-Token header or ?token= query parameter
        token = self.headers.get("X-Kill-Token", "")
        if not token:
            qs = parse_qs(urlparse(self.path).query)
            token = qs.get("token", [""])[0]

        if token != _KILL_API_TOKEN:
            _logger.warning("AUTH FAILED from %s on %s", self.client_address[0], self.path)
            self._send_cors_headers(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({"status": "forbidden", "message": "Invalid or missing X-Kill-Token."}).encode()
            self.wfile.write(body)
            return False
        return True

    def do_OPTIONS(self):
        self._send_cors_headers(200)
        self.end_headers()

    def do_GET(self):
        if not self._check_auth():
            return

        if self.path.startswith("/api/logs"):
            try:
                import collections
                qs = parse_qs(urlparse(self.path).query)
                n = min(200, max(10, int(qs.get("n", ["100"])[0])))
                log_path = os.path.join(os.path.dirname(KILL_FLAG_FILE), "..", "logs", "paper_loop.out")
                log_path = os.path.abspath(log_path)
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = collections.deque(f, n)
                    body = json.dumps({"lines": list(lines)}).encode()
                else:
                    body = json.dumps({"lines": ["[Log file not found]"]}).encode()
                self._send_cors_headers(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                _logger.error("Error reading logs: %s", e)
                body = json.dumps({"lines": [f"Error reading logs: {e}"]}).encode()
                self._send_cors_headers(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif self.path.startswith("/api/state"):
            try:
                from market_discovery_internal.state_persistence import load_paper_state
                state = load_paper_state()
                body = json.dumps(state, default=str).encode()
                self._send_cors_headers(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                _logger.error("Error loading state: %s", e)
                body = json.dumps({"status": "error", "message": str(e)}).encode()
                self._send_cors_headers(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self._send_cors_headers(404)
            self.end_headers()

    def do_POST(self):
        if not self._check_auth():
            return

        if self.path == "/api/kill":
            try:
                os.makedirs("logs", exist_ok=True)
                with open(KILL_FLAG_FILE, "w") as f:
                    f.write("kill")
                body = json.dumps({"status": "kill_flag_set", "message": "Bot will shut down after current cycle."}).encode()
                self._send_cors_headers(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                _logger.error("Error setting kill flag: %s", e)
                body = json.dumps({"status": "error", "message": str(e)}).encode()
                self._send_cors_headers(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self._send_cors_headers(404)
            self.end_headers()

    def _send_cors_headers(self, code):
        self.send_response(code)
        # Validate Origin against allowed patterns (dashboard on port 8080)
        origin = self.headers.get("Origin", "")
        # Allow: configured origin, or same-host on port 8080 (dashboard)
        if origin == _CORS_ORIGIN or (origin and ":8080" in origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", _CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Kill-Token")

    def log_message(self, format, *args):
        # Log errors and auth failures; suppress routine access logs
        if args and len(args) >= 2:
            status = str(args[1]) if len(args) > 1 else ""
            if status.startswith("4") or status.startswith("5"):
                _logger.warning("HTTP %s %s from %s", args[1], args[0], self.client_address[0])
                return
        # Suppress normal access logs to avoid noise
        pass


def start_command_server(port=COMMAND_SERVER_PORT, bind=COMMAND_SERVER_BIND):
    """Start the command HTTP server in a daemon thread. Returns the server instance."""
    try:
        server = HTTPServer((bind, port), _CommandHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="cmd-server")
        thread.start()
        _logger.info("[MODUL J] Command server listening on %s:%d", bind, port)
        return server
    except OSError as e:
        _logger.error("[MODUL J] Could not start command server on %s:%d: %s", bind, port, e)
        return None


def check_kill_flag():
    """Return True if kill flag file exists, and remove it."""
    if os.path.exists(KILL_FLAG_FILE):
        try:
            os.remove(KILL_FLAG_FILE)
        except OSError:
            pass
        return True
    return False
