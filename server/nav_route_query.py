"""Opt-in, loopback-only HTTP query for NAV-06 Rabao candidate route previews.

This reads the derived NVMe SQLite only. It does not publish a release, approve
candidate edges, scan HDD telemetry, control UC, or run on import.
"""
import argparse
import hmac
import ipaddress
import json
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import derived_navigation_db
import nav_route_preview

SCHEMA_VERSION = 1
ZONE = 247  # Supervised Rabao pilot only
MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_WAYPOINTS = 101  # Existing UC one-shot executor's hard cap
PATH = "/v1/nav/preview"
NO_ROUTE_REASONS = (
    ("No recorded navigation node", "NO_NEARBY_NODE"),
    ("No directed candidate path", "NO_DIRECTED_PATH"),
    ("Start and destination snap to the same node", "SAME_NODE"),
    ("Candidate detour is too long", "DETOUR_LIMIT"),
    ("Candidate route exceeds waypoint limit", "WAYPOINT_LIMIT"),
)


def route_query(nav_db, request):
    """Pure query adapter; returns a status, response object, and HTTP status."""
    if (type(request) is not dict
            or set(request) != {"zone", "from_xyz", "to_xyz"}
            or type(request.get("zone")) is not int
            or request["zone"] != ZONE):
        return HTTPStatus.BAD_REQUEST, {
            "schema_version": SCHEMA_VERSION, "status": "INVALID_REQUEST",
            "reason": "RABAO_ZONE_AND_XYZ_REQUIRED",
        }
    try:
        # Reject implicit coordinate strings and booleans at the wire boundary.
        for name in ("from_xyz", "to_xyz"):
            value = request[name]
            if type(value) is not list:
                raise ValueError("Coordinate must be a numeric XYZ array")
            nav_route_preview.coordinates(value)
    except ValueError:
        return HTTPStatus.BAD_REQUEST, {
            "schema_version": SCHEMA_VERSION, "status": "INVALID_REQUEST",
            "reason": "INVALID_XYZ",
        }
    try:
        preview = nav_route_preview.preview(
            nav_db, ZONE, request["from_xyz"], request["to_xyz"],
            snap_radius=1.0, max_distance=100.0, max_stretch=3.0,
        )
    except ValueError as exc:
        for prefix, reason in NO_ROUTE_REASONS:
            if str(exc).startswith(prefix):
                return HTTPStatus.OK, {
                    "schema_version": SCHEMA_VERSION, "status": "NO_ROUTE",
                    "reason": reason, "zone": ZONE,
                    "approved_for_movement": False,
                }
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "schema_version": SCHEMA_VERSION, "status": "GRAPH_UNAVAILABLE",
            "reason": "INVALID_OR_UNPUBLISHED_GRAPH",
        }
    except (OSError, sqlite3.Error):
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "schema_version": SCHEMA_VERSION, "status": "GRAPH_UNAVAILABLE",
            "reason": "GRAPH_READ_FAILED",
        }

    if len(preview["waypoints"]) > MAX_RESPONSE_WAYPOINTS:
        return HTTPStatus.OK, {
            "schema_version": SCHEMA_VERSION, "status": "NO_ROUTE",
            "reason": "WAYPOINT_LIMIT", "zone": ZONE,
            "approved_for_movement": False,
        }
    # The current live graph is candidate-only: this response is NOT a command
    # to move and may not be consumed as executable navigation.
    return HTTPStatus.OK, {
        "schema_version": SCHEMA_VERSION,
        "status": "ROUTE_PREVIEW",
        "zone": ZONE,
        "approved_for_movement": False,
        "preview": preview,
    }


def make_handler(nav_db, token):
    """Build a request handler without creating files, sockets, or threads."""
    if not isinstance(token, str) or len(token) < 24:
        raise ValueError("NAV_QUERY_TOKEN must have at least 24 characters")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Prevent request/header logging, including authorization values.
            pass

        def send_json(self, code, payload):
            body = json.dumps(payload, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if code == HTTPStatus.UNAUTHORIZED:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != PATH:
                return self.send_json(HTTPStatus.NOT_FOUND, {
                    "status": "NOT_FOUND",
                })
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            if not hmac.compare_digest(supplied, expected):
                return self.send_json(HTTPStatus.UNAUTHORIZED, {
                    "status": "UNAUTHORIZED",
                })
            if self.headers.get_content_type() != "application/json":
                return self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {
                    "status": "INVALID_REQUEST", "reason": "JSON_REQUIRED",
                })
            raw_length = self.headers.get("Content-Length")
            if raw_length is None or not raw_length.isascii() or not raw_length.isdecimal():
                return self.send_json(HTTPStatus.LENGTH_REQUIRED, {
                    "status": "INVALID_REQUEST", "reason": "LENGTH_REQUIRED",
                })
            length = int(raw_length)
            if length < 1 or length > MAX_REQUEST_BYTES:
                return self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                    "status": "INVALID_REQUEST", "reason": "REQUEST_TOO_LARGE",
                })
            try:
                request = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, ValueError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {
                    "status": "INVALID_REQUEST", "reason": "INVALID_JSON",
                })
            code, payload = route_query(nav_db, request)
            return self.send_json(code, payload)

        def do_GET(self):
            return self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {
                "status": "METHOD_NOT_ALLOWED",
            })

    return Handler


def bind_address(host):
    """Allow only a specific local/private IPv4 interface, never wildcard/public."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Host must be a numeric private or loopback IPv4 address") from exc
    private_lan = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    if (not isinstance(address, ipaddress.IPv4Address)
            or not (address.is_loopback or any(
                address in block for block in private_lan))):
        raise ValueError("Host must be a specific private or loopback IPv4 address")
    return str(address)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nav-db", required=True,
                        help="Explicit derived NVMe SQLite; NEVER raw capture DB")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Specific private LAN IP, default loopback")
    parser.add_argument("--port", type=int, default=18788)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be in the range 1..65535")
    try:
        host = bind_address(args.host)
    except ValueError as exc:
        parser.error(str(exc))
    token = os.environ.get("UC_NAV_QUERY_TOKEN", "")
    if len(token) < 24:
        parser.error("Set UC_NAV_QUERY_TOKEN to a strong secret (24+ chars)")
    # Fail before opening the listener if the target is not a derived graph.
    derived_navigation_db.inspect(args.nav_db)
    handler = make_handler(args.nav_db, token)
    # Bind ONLY the named private interface; never expose on all networks.
    with ThreadingHTTPServer((host, args.port), handler) as server:
        print("NAV-06 candidate-preview listener on " + host + ":"
              + str(args.port), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
