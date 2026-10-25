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


def upload(url, token, source_name, raw, records, timeout=15, offset=None):
    digest = hashlib.sha256(raw).hexdigest()
    req = Request(
        url.rstrip("/") + "/v1/ingest",
        data=raw,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/x-ndjson",
            "X-UCAP-Source": source_name,
            **({"X-UCAP-Offset": str(offset)} if offset is not None else {}),
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
    # Older uploader versions tracked acknowledgement offsets, but did not
    # send the byte ranges needed for whole-file archive verification. Replay
    # them once (server deduplicates) to establish authoritative receipts.
    if saved and saved.get("receipted") is not True:
        LOG.info("Reindexing legacy upload receipts for %s", path.name)
        offset = 0
    else:
        offset = int(saved.get("offset", 0))
    if offset < 0 or size < offset:
        LOG.warning("Capture shrank; resetting upload offset for %s", path.name)
        offset = 0
    total = 0
    while True:
        chunk, end, lines = read_batch(path, offset)
        if not lines:
            break
        result = upload(url, token, path.name, chunk, lines, timeout, offset)
        # Server has already committed. Crash before state write just replays
        # this batch, which the server safely deduplicates.
        offset = end
        state["files"][key] = {"offset": offset, "receipted": True}
        save_state(state_path, state)
        total += lines
        LOG.info("%s: sent %d records (%d new, %d already stored)",
                 path.name, lines, result["inserted"], result["duplicates"])
    return total


def get_final_record(path, size):
    """Final line must certify a closed recording; never guess closure from age."""
    with open(path, "rb") as fp:
        fp.seek(max(0, size - MAX_LINE_BYTES - 1))
        tail = fp.read()
    if not tail.endswith(b"\n"):
        return None
    final_line = tail.splitlines()[-1]
    try:
        final = json.loads(final_line)
    except (UnicodeError, ValueError):
        return None
    if (not isinstance(final, dict) or final.get("event") != "session_stop"
            or type(final.get("sequence")) is not int
            or not isinstance(final.get("session"), str)):
        return None
    if path.name != final["session"] + ".jsonl":
        return None
    return final


def digest_file(path):
    result = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def request_file_verification(url, token, path, size, checksum, session, timeout):
    manifest = json.dumps({
        "source_file": path.name, "session": session,
        "size": size, "sha256": checksum,
    }, separators=(",", ":")).encode("utf-8")
    req = Request(
        url.rstrip("/") + "/v1/verify-file",
        method="POST",
        data=manifest,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("Unexpected verification status")
        result = json.load(response)
    if (result.get("verified") is not True or
            result.get("source_file") != path.name or
            result.get("session") != session or
            result.get("size") != size or result.get("sha256") != checksum):
        raise ValueError("Server did not verify the exact recording")
    return result


def cleanup_file(path, state, state_path, url, token, retention_days=7, timeout=15,
                 now=None):
    """Fail closed unless a closed old file exactly matches server evidence."""
    if now is None:
        now = time.time()
    path = Path(path)
    st = path.stat()
    if now - st.st_mtime < retention_days * 86400:
        return False
    key = os.path.normcase(str(path.resolve()))
    if int(state["files"].get(key, {}).get("offset", 0)) != st.st_size:
        return False
    final = get_final_record(path, st.st_size)
    if final is None:
        return False
    checksum = digest_file(path)
    # Whole-file verification reconstructs the original bytes from archived
    # chunks, checks contiguous sequence coverage, the SQLite event ledger,
    # and a terminal session_stop before the server attests anything.
    request_file_verification(url, token, path, st.st_size, checksum,
                              final["session"], timeout)
    # A writer could theoretically change a file during verification. Refuse
    # to delete unless metadata remains identical after the remote roundtrip.
    after = path.stat()
    if (after.st_size != st.st_size or after.st_mtime_ns != st.st_mtime_ns
            or getattr(after, "st_ino", 0) != getattr(st, "st_ino", 0)):
        LOG.warning("Retaining file changed during verification: %s", path.name)
        return False
    path.unlink()
    state["files"].pop(key, None)
    save_state(state_path, state)
    LOG.info("Retention cleanup removed verified closed file: %s", path.name)
    return True


# Throttle retention checks per path, including legacy and crash-orphan files.
_CLEANUP_CHECKED_AT = {}


def poll(directories, state, state_path, url, token, timeout=15,
         retention_days=None):
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
            if retention_days is not None:
                now = time.time()
                if now - _CLEANUP_CHECKED_AT.get(key, 0) >= 3600:
                    _CLEANUP_CHECKED_AT[key] = now
                    try:
                        cleanup_file(path, state, state_path, url, token,
                                     retention_days, timeout, now)
                    except (OSError, ValueError, HTTPError, URLError) as exc:
                        # A failed verification must never delete or block
                        # other uploads; retry no sooner than the next hour.
                        LOG.warning("Retention skipped for %s: %s", path.name, exc)
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
    p.add_argument("--retention-days", type=float, default=None,
                   help="Opt in to verified closed-file cleanup after N days (recommended: 7); off by default")
    args = p.parse_args()
    token = os.getenv("UCAP_API_TOKEN", "")
    if len(token) < 16 or token.startswith("REPLACE_"):
        p.error("Set UCAP_API_TOKEN environment variable (at least 16 characters)")
    if not args.server.startswith(("http://", "https://")):
        p.error("--server must be an http:// or https:// URL")
    if args.interval <= 0 or args.timeout <= 0:
        p.error("--interval and --timeout must be positive")
    if args.retention_days is not None and args.retention_days < 1:
        p.error("--retention-days must be at least one day")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state(args.state)
    while True:
        try:
            poll(args.capture_dir, state, args.state, args.server, token,
                 args.timeout, args.retention_days)
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
