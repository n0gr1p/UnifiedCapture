"""Independent Windows-friendly uploader for all UnifiedCapture JSONL files.

Does not touch Windower memory or invoke any gameplay action.
"""
import argparse
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("ucap-uploader")
MAX_UPLOAD_BYTES = 512 * 1024
MAX_UPLOAD_LINES = 512
MAX_LINE_BYTES = 262144


def load_state(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") == 1 and isinstance(data.get("files"), dict):
            return data
    except (FileNotFoundError, ValueError, OSError, AttributeError):
        pass
    return {"version": 1, "files": {}}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".ucap-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(state, fp, sort_keys=True)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_batch(file_path, offset):
    """Read complete records only; ignore an active writer's partial last line."""
    content = bytearray()
    lines = 0
    end = offset
    with open(file_path, "rb") as fp:
        fp.seek(offset)
        while lines < MAX_UPLOAD_LINES:
            before = fp.tell()
            line = fp.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("Oversized JSONL line in " + str(file_path))
            if not line.endswith(b"\n"):
                break
            if len(content) + len(line) > MAX_UPLOAD_BYTES:
                if not content:
                    raise ValueError("One JSONL record exceeds uploader batch limit")
                fp.seek(before)
                break
            content.extend(line)
            lines += 1
            end = fp.tell()
    return bytes(content), end, lines


def upload(url, token, source_name, raw, records, timeout=15):
    digest = hashlib.sha256(raw).hexdigest()
    req = Request(
        url.rstrip("/") + "/v1/ingest",
        data=raw,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/x-ndjson",
            "X-UCAP-Source": source_name,
        },
    )
    with urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("Unexpected server status: " + str(response.status))
        result = json.load(response)
    if result.get("sha256") != digest or result.get("count") != records:
        raise ValueError("Server response failed batch acknowledgement verification")
    if result.get("inserted", 0) + result.get("duplicates", 0) != records:
        raise ValueError("Server did not account for every record")
    return result


def upload_file(path, state, state_path, url, token, timeout=15):
    path = Path(path)
    key = os.path.normcase(str(path.resolve()))
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0
    saved = state["files"].get(key, {})
    offset = int(saved.get("offset", 0))
    if offset < 0 or size < offset:
        LOG.warning("Capture shrank; resetting upload offset for %s", path.name)
        offset = 0
    total = 0
    while True:
        chunk, end, lines = read_batch(path, offset)
        if not lines:
            break
        result = upload(url, token, path.name, chunk, lines, timeout)
        # Server has already committed. Crash before state write just replays
        # this batch, which the server safely deduplicates.
        offset = end
        state["files"][key] = {"offset": offset}
        save_state(state_path, state)
        total += lines
        LOG.info("%s: sent %d records (%d new, %d already stored)",
                 path.name, lines, result["inserted"], result["duplicates"])
    return total


def poll(directories, state, state_path, url, token, timeout=15):
    seen = set()
    total = 0
    for directory in directories:
        root = Path(directory)
        if not root.is_dir():
            LOG.warning("Capture directory missing: %s", root)
            continue
        for path in sorted(root.glob("*.jsonl")):
            key = os.path.normcase(str(path.resolve()))
            if key in seen:
                continue
            seen.add(key)
            total += upload_file(path, state, state_path, url, token, timeout)
    return total


def main():
    p = argparse.ArgumentParser(description="Upload UnifiedCapture JSONL in the background")
    p.add_argument("--capture-dir", action="append", required=True,
                   help="Path to data/captures (repeat for separate Windower installations)")
    p.add_argument("--server", required=True, help="Ingestion API base URL")
    p.add_argument("--state", default="./ucap-uploader-state.json",
                   help="Local checkpoint file; keep between restarts")
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--once", action="store_true",
                   help="Upload all available complete lines, then exit")
    args = p.parse_args()
    token = os.getenv("UCAP_API_TOKEN", "")
    if len(token) < 16 or token.startswith("REPLACE_"):
        p.error("Set UCAP_API_TOKEN environment variable (at least 16 characters)")
    if not args.server.startswith(("http://", "https://")):
        p.error("--server must be an http:// or https:// URL")
    if args.interval <= 0 or args.timeout <= 0:
        p.error("--interval and --timeout must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state(args.state)
    while True:
        try:
            poll(args.capture_dir, state, args.state, args.server, token, args.timeout)
        except (OSError, ValueError, HTTPError, URLError) as exc:
            # No checkpoint advancement on any failed upload.
            LOG.warning("Upload unsuccessful; retry will resume from last committed offset: %s", exc)
            if args.once:
                raise SystemExit(1) from exc
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
