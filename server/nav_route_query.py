"""Read-only, bounded NAV-06 directed candidate route query adapter.

Called by the existing authenticated UnifiedCapture ingestion HTTP API on
port 8787. No independent listener, token, raw HDD access or movement.
"""
import sqlite3
from http import HTTPStatus

import nav_route_preview

SCHEMA_VERSION = 1
ZONE = 247
MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_WAYPOINTS = 101
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
