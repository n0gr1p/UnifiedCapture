"""Authenticated LAN JSONL ingestion API (Python 3.11+, standard library)."""
import argparse
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from navigation import (NavigationQueryError, dataset as navigation_dataset,
                        zones as navigation_zones, transitions as navigation_transitions,
                        sessions as navigation_sessions, route_events as navigation_route_events)

from storage import IngestError, MAX_BODY_BYTES, ingest, initialize, recover, stats, verify_file

LOG = logging.getLogger("unifiedcapture")


def serve(host, port, db_path, archive_dir, token):
    if not token or len(token) < 16 or token.startswith("REPLACE_"):
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
                return
            # Fixed read-only telemetry queries. No SQL, file path, or mutation
            # input is ever accepted from MCP/HTTP callers.
            allowed = {
                "/v1/nav/dataset": set(),
                "/v1/nav/zones": {"limit", "offset"},
                "/v1/nav/transitions": {"limit", "offset", "from_zone"},
                "/v1/nav/sessions": {"limit", "offset", "zone"},
                "/v1/nav/route-events": {"session", "after_sequence", "limit", "include_interactions"},
            }
            if path not in allowed:
                self.respond(404, {"error": "not found"})
                return
            try:
                params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(params) - allowed[path] or any(len(v) != 1 for v in params.values()):
                    raise NavigationQueryError("unsupported or repeated query parameter")

                def integer(name, default=None):
                    if name not in params:
                        return default
                    raw = params[name][0]
                    if not raw.isascii() or not raw.isdecimal():
                        raise NavigationQueryError(name + " must be an unsigned integer")
                    return int(raw)

                limit = integer("limit", 50)
                offset = integer("offset", 0)
                if path == "/v1/nav/dataset":
                    result = navigation_dataset(db_path)
                elif path == "/v1/nav/zones":
                    result = navigation_zones(db_path, limit=limit, offset=offset)
                elif path == "/v1/nav/transitions":
                    result = navigation_transitions(db_path, limit=limit, offset=offset,
                                                    from_zone=integer("from_zone"))
                elif path == "/v1/nav/sessions":
                    result = navigation_sessions(db_path, zone=integer("zone"),
                                                 limit=limit, offset=offset)
                else:
                    if "session" not in params:
                        raise NavigationQueryError("session is required")
                    mode = params.get("include_interactions", ["true"])[0]
                    if mode not in ("true", "false"):
                        raise NavigationQueryError("include_interactions must be true or false")
                    result = navigation_route_events(
                        db_path, params["session"][0],
                        after_sequence=integer("after_sequence", 0),
                        limit=limit, include_interactions=mode == "true")
                self.respond(200, result)
            except NavigationQueryError as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                LOG.exception("Navigation query failure")
                self.respond(500, {"error": "navigation query failed"})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ("/v1/ingest", "/v1/verify-file"):
                self.respond(404, {"error": "not found"})
                return
            if not self.authorized():
                return
            expected = ("application/json" if path == "/v1/verify-file"
                        else "application/x-ndjson")
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != expected:
                self.respond(415, {"error": "expected " + expected})
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
                if path == "/v1/verify-file":
                    try:
                        manifest = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeError) as exc:
                        raise IngestError("Malformed verification JSON") from exc
                    self.respond(200, verify_file(db_path, archive_dir, manifest))
                else:
                    offset_header = self.headers.get("X-UCAP-Offset")
                    if offset_header is not None:
                        if not offset_header.isascii() or not offset_header.isdigit():
                            raise IngestError("Invalid upload offset")
                        offset = int(offset_header)
                    else:
                        offset = None  # Backward compatible; no deletion receipt.
                    self.respond(200, ingest(
                        db_path, archive_dir, raw,
                        self.headers.get("X-UCAP-Source"), offset))
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
