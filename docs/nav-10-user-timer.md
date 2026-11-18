# NAV-10: opt-in user systemd timer for bounded candidate backfill

**Status:** Configuration files are committed to draft PR #18, but **no unit has been installed, enabled, or started on uuserver**. Keep the ingestion service running. All work here produces *candidate-only* NVMe graph records, not approved navigation edges.

## Proven inputs and constraints (2026-11-18)

- Running Compose project: `/home/peter/UnifiedCapture`.
- Tested navigation working tree: `/home/peter/UnifiedCapture-NavDev` (may be detached at a PR branch commit).
- `peter` is already a member of the `docker` group (`root:docker srw-rw----` socket), and the sudo-free UID-10001 reader/host writer **completed a live dry run** from source rowid 18400 to proposed rowid 20400. No new privileges should be created.
- Latest verified committed NVMe cursor is **18400**; initial raw high-water was **31153494** (roughly 31.1 million source rowids behind, before further live capture).
- Five manually supervised windows of 2,000 rows successfully imported 10,000 rows with healthy post-run `/health`; no proven concurrent ingestion latency benchmark. Late out-of-order session chunks still produce `OUT_OF_ORDER_SESSION_SEQUENCE` until targeted repair is built.

## Proposed finite schedule (conservative initial setting)

- User-owned *oneshot* service at `deploy/systemd/user/unifiedcapture-nav-backfill.service`.
- **At most 5 batches x 2,000 events (10,000 rows) per invocation**; pause 5 seconds between batches. The cursor is independently verified after each committed batch.
- **First start five minutes after timer activation; subsequent starts at least 15 minutes after the previous service finished**. No missed-run catch-up (`Persistent=false`); concurrent service runs are prevented by systemd, and the worker has its own advisory lock.
- The service applies `Nice=10`, `IOSchedulingClass=idle` and a 180-second startup timeout, and it runs as the existing logged-in host user. The bounded source reader runs inside Docker as UID 10001. No service restart or raw capture DB writes.
- Backlog is roughly 3,114 timer runs at 10,000 events each, approximately 32+ days at this conservative cadence. That's only a **theoretical lower bound**: new live events, reader pauses, source gaps, processing errors, throughput variation and offline periods may extend it.
- User-level systemd units run when the user's systemd user manager runs. **Persistence after logout/reboot depends on linger** (`loginctl show-user peter -p Linger`); do not silently enable linger, and obtain operator approval before changing login policy.

## Safe field acceptance, one checkpoint at a time

1. Confirm the user manager and linger state (read-only):

   ```sh
   loginctl show-user peter -p Linger && systemctl --user is-system-running
   ```

2. Fetch the latest PR #18 into the existing detached NavDev checkout, then **copy** (not symlink) the two unit files into `~/.config/systemd/user/`. Do not touch `/etc/systemd/system/`, make service changes or modify Docker permissions.

   ```sh
   git -C ~/UnifiedCapture-NavDev fetch origin feature/nav-10-incremental-worker-20261118 && git -C ~/UnifiedCapture-NavDev switch --detach FETCH_HEAD
   mkdir -p ~/.config/systemd/user
   install -m 0644 ~/UnifiedCapture-NavDev/deploy/systemd/user/unifiedcapture-nav-backfill.service ~/.config/systemd/user/
   install -m 0644 ~/UnifiedCapture-NavDev/deploy/systemd/user/unifiedcapture-nav-backfill.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   ```

   **This installs definitions only**: the timer remains disabled and no graph writes begin.

3. Verify parsed units and disabled status before any opt-in:

   ```sh
   systemctl --user cat unifiedcapture-nav-backfill.service unifiedcapture-nav-backfill.timer
   systemctl --user is-enabled unifiedcapture-nav-backfill.timer
   ```

4. Only with explicit operator agreement, opt into the scheduled five-minute first start and finite 15-minute follow-up:

   ```sh
   systemctl --user enable --now unifiedcapture-nav-backfill.timer
   systemctl --user list-timers --all | grep unifiedcapture-nav-backfill
   ```

5. Observe the first run and capture status/health rather than assuming zero ingestion impact:

   ```sh
   journalctl --user -u unifiedcapture-nav-backfill.service -n 40 --no-pager
   systemctl --user status unifiedcapture-nav-backfill.timer --no-pager
   curl -fsS http://192.168.96.45:8787/health
   ```

6. **Stop future runs** without deleting graph data or resetting the cursor:

   ```sh
   systemctl --user disable --now unifiedcapture-nav-backfill.timer
   ```

   To interrupt a currently running service, separately use `systemctl --user stop unifiedcapture-nav-backfill.service`; stopping a service mid-batch must be checked via the persisted cursor before resuming. The worker's deterministic evidence IDs protect replay, but there is no full-history session repair yet.

**Failure policy:** The finite worker stops and exits nonzero on any untrusted source row order, reader/process error, stale cursor, or other unexpected failure. Inspect `journalctl --user -u unifiedcapture-nav-backfill.service`, disable the timer while investigating, and do not bypass `OUT_OF_ORDER_SESSION_SEQUENCE`. The timer's cadence may retry failed services; it is not self-healing.

**No executable routing:** Cross-zone planner, transport integration and RemoteXI activity orchestration are separate NAV milestones.
