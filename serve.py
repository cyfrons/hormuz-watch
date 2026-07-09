"""
serve.py

Entry point for free-tier PaaS hosting (Render and similar) that only
offers its free plan to services that answer HTTP requests. Runs a
tiny health-check server in a background thread purely so the
platform classifies this as a valid web service, while the real work
- ais_listener.listen() - runs as normal on the main thread.

Running locally, on a Raspberry Pi, or on a plain VPS? You don't need
this file at all - just run `python3 ais_listener.py` directly.

Render (and most similar platforms) inject a PORT environment
variable and expect the service to listen on it - that's the only
reason this file exists.
"""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import ais_listener


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/status"):
            self._serve_status()
        else:
            self._serve_health()

    def _serve_health(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"hormuz-watch listener is running - see /status for live data\n")

    def _serve_status(self):
        try:
            with open(ais_listener.SIGHTINGS_PATH) as f:
                payload = json.load(f)
        except FileNotFoundError:
            payload = {"note": "sightings.json not written yet - listener may still be connecting"}
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # don't let health-check pings flood the platform's log view


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.run(ais_listener.listen())
