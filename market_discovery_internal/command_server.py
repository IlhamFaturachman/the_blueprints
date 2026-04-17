"""
[MODUL J] Emergency Kill-Switch — HTTP command server.
Listens on COMMAND_SERVER_PORT (default 8083) for POST /api/kill.
Writing the kill flag causes the paper loop to exit cleanly.

Security: Requires X-Kill-Token header matching KILL_API_TOKEN env var.
CORS is restricted to the configured origin (default: localhost only).
"""

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

KILL_FLAG_FILE = os.getenv("KILL_FLAG_FILE", "logs/kill.flag")
COMMAND_SERVER_PORT = int(os.getenv("COMMAND_SERVER_PORT", "8083"))
# Auth token — set KILL_API_TOKEN in .env; if unset, kill endpoint is disabled.
_KILL_API_TOKEN = os.getenv("KILL_API_TOKEN", "")
# CORS: restrict to your dashboard origin (default: same-origin via nginx proxy)
_CORS_ORIGIN = os.getenv("COMMAND_SERVER_CORS_ORIGIN", "http://localhost:8080")


class _CommandHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._send_cors_headers(200)
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/kill":
            # Auth guard: require X-Kill-Token header
            if not _KILL_API_TOKEN:
                self._send_cors_headers(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"disabled","message":"Kill endpoint disabled: KILL_API_TOKEN not configured."}')
                return

            token = self.headers.get("X-Kill-Token", "")
            if token != _KILL_API_TOKEN:
                self._send_cors_headers(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"forbidden","message":"Invalid or missing X-Kill-Token."}')
                return

            try:
                os.makedirs("logs", exist_ok=True)
                with open(KILL_FLAG_FILE, "w") as f:
                    f.write("kill")
                self._send_cors_headers(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"kill_flag_set","message":"Bot will shut down after current cycle."}')
            except Exception as e:
                self._send_cors_headers(500)
                self.end_headers()
                self.wfile.write(f'{{"status":"error","message":"{e}"}}'.encode())
        else:
            self._send_cors_headers(404)
            self.end_headers()

    def _send_cors_headers(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", _CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Kill-Token")

    def log_message(self, format, *args):
        pass  # Suppress default HTTP access logs


def start_command_server(port=COMMAND_SERVER_PORT):
    """Start the command HTTP server in a daemon thread. Returns the server instance."""
    try:
        server = HTTPServer(("0.0.0.0", port), _CommandHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="cmd-server")
        thread.start()
        print(f"[MODUL J] Command server listening on port {port}")
        return server
    except OSError as e:
        print(f"[MODUL J] Could not start command server on port {port}: {e}")
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
