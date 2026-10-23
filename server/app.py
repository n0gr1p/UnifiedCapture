"""Authenticated LAN JSONL ingestion API (Python 3.11+, standard library)."""
import argparse
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from storage import IngestError, MAX_BODY_BYTES, ingest, initialize, recover, stats

LOG = logging.getLogger("unifiedcapture")


def serve(host, port, db_path, archive_dir, token):
    if not token or len(token) < 16:
        raise ValueError("UCAP_API_TOKEN must be set to at least 16 characters")
    initialize(db_path)
    replayed = recover(db_path, archive_dir)
    LOG.info("Archive recovery completed; %d missing events imported", replayed)

    class Handler(BaseHTTPRequestHandler):
        server_version = "UnifiedCaptureIngest/1"
        def log_message(self, fmt, *args):
            LOG.info("%s - %s", self.client_address[0], fmt % args)

        def respond(self, code, payload):
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            if not hmac.compare_digest(supplied, token):
                self.respond(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.respond(200, {"status": "ok", "schema": 1})
                return
            if not self.authorized():
                return
            if path == "/v1/stats":
                self.respond(200, stats(db_path))
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self):
            if urlsplit(self.path).path != "/v1/ingest":
                self.respond(404, {"error": "not found"})
                return
            if not self.authorized():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/x-ndjson":
                self.respond(415, {"error": "expected application/x-ndjson"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.respond(400, {"error": "chunked requests unsupported"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 1 or length > MAX_BODY_BYTES:
                self.respond(413, {"error": "invalid batch size"})
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                self.respond(400, {"error": "incomplete batch"})
                return
            try:
                self.respond(200, ingest(
                    db_path, archive_dir, raw, self.headers.get("X-UCAP-Source")))
            except IngestError as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                LOG.exception("Ingestion failure")
                self.respond(500, {"error": "internal error"})

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    LOG.info("Listening on %s:%d (archive=%s, db=%s)",
             host, httpd.server_address[1], archive_dir, db_path)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UnifiedCapture ingest service")
    parser.add_argument("--host", default=os.getenv("UCAP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("UCAP_PORT", "8787")))
    parser.add_argument("--db", default=os.getenv("UCAP_DB", "/data/capture.sqlite3"))
    parser.add_argument("--archive", default=os.getenv("UCAP_ARCHIVE", "/data/archive"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(args.host, args.port, args.db, args.archive, os.getenv("UCAP_API_TOKEN", ""))
