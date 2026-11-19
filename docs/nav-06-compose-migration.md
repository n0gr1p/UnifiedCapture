# NAV-06: integrate query API into live UnifiedCapture Compose project

This is a *narrow deployment overlay* based on
`feature/navigation-readonly-queries`, the existing Ubuntu ingestion
checkout. It copies the NAV-06 candidate preview module/tests from
[UCapture PR #19](https://github.com/n0gr1p/UnifiedCapture/pull/19)
without pulling NAV-10/backfill implementation changes into ingestion.

## Invariants

- The existing `ingest` service in `compose.yaml` is byte-for-byte
  unchanged. The untracked `compose.override.yaml` is **not modified**;
  it continues pointing the ingestion volume to
  `/home/peter/bulk-storage/UnifiedCapture/server-data`.
- The new `nav-query` service is part of this **same Docker Compose
  project**, with `restart: unless-stopped` and its own image. It does not
  recreate, stop or depend on `ingest`.
- The *only* data mount on the new service is the derived
  `../UnifiedCapture-NavDev/nav-data:/nav-data:ro` directory,
  **not** the HDD-backed ingest volume.
- The standalone pilot secret at
  `~/.config/unifiedcapture-nav/query.env` is reused as an `env_file`.
  Never check it into Git or print its contents. The endpoint itself is
  restricted to an explicit loopback or RFC1918 IPv4, with bearer auth.
- The graph is still candidate-only, `release_status=not_built` and
  `approved_for_movement=false`. No UC movement is authorized.

## Single-service migration (operator-controlled)

1. On Ubuntu, check the current live branch is
   `feature/navigation-readonly-queries` with only untracked
   `compose.override.yaml`, then fetch the
   `feature/nav-06-compose-live-integration-20261119` branch and switch
   to it. **Do not run `docker compose up` for the full stack.**
2. Run `docker compose config --quiet`; inspect
   `docker compose config --format json` to verify the new nav-query
   only mounts the NVMe derived directory and binds the private IP, while
   `ingest` keeps the HDD override. Validate access permissions without
   changing raw telemetry.
3. Stop and disable **only** the temporary standalone service to release
   port 18788:
   `systemctl --user disable --now uc-nav-query.service`.
   Keep the existing token file and systemd unit for rollback.
4. Launch **only** the nav-query container from the live Compose project:
   `docker compose up -d --no-deps --build nav-query`.
   Then `docker compose ps nav-query`, check the nav service logs for a
   local bind error, and verify an unauthenticated POST from Windows
   returns `401`.
5. Confirm authentication and a Rabao `ROUTE_PREVIEW` in shadow-only
   mode before any consideration of movement. The newer shorter route
   has not been field validated.

Rollback: `docker compose stop nav-query`, then
`systemctl --user enable --now uc-nav-query.service`.
Do not touch the `ingest` service or the NAV-10 backfill timer.

Plain HTTP on a private LAN does not encrypt bearer tokens. Restrict
TCP/18788 at the host firewall to the gaming PC; never forward it from the
router to the Internet. TLS is preferable if LAN compromise is in scope.
