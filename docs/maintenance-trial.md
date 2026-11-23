# Documented cloud replacement trial

Everything in this file comes from **one** replacement run, id `a90704c3ce72`
(reason `documented maintenance trial`). The log excerpts are trimmed from
`logs/maintenance.log`; the machine-readable state is
`maintenance_state/run_state.json`. Secrets (API keys, passwords, full
connection strings) are replaced with `<redacted>` in every pasted line.

Source instance: Neon project `wispy-cake-58578289` (`room-booking-prod`, eu-central-1).
Target instance: Neon project `quiet-tree-18455871` (`room-booking-prod-a90704c3`).
Application: Render service `srv-dc1vhfqjnfac739trp50` (`room-booking-api`),
`https://room-booking-api-0khx.onrender.com`.

## 1. How the run was performed

```bash
# the maintenance CLI on a host that can reach the provider's PostgreSQL port
python -m maintenance.run check            # status and expiry information
python -m maintenance.run replace --reason "documented maintenance trial" --confirm
python -m maintenance.run state            # persisted run state afterwards
```

`--confirm` is the explicit authorisation. `watch` only observes: it never
replaces anything, and the scheduled APScheduler job only reports status.

## 2. Scheduled asynchronous status check

```
2026-11-23 19:50:01,195 INFO     maintenance.controller: Status check: {'instance_id': 'wispy-cake-58578289', 'instance_name': 'room-booking-prod', 'ready': True, 'expires_at': None, 'expires_in_hours': None, 'backend_ok': True, 'maintenance_mode': False, 'bookings': 3, 'checked_at': '2026-11-23T11:50:01+00:00'}
```

- Instance identifier: `wispy-cake-58578289`
- Expiry information reported by the provider: `expires_at = None`,
  `expires_in_hours = None` (the free plan suspends an idle compute but does not
  expire the project)
- Backend health and reservation count at check time: `backend_ok = True`,
  `maintenance_mode = False`, `bookings = 3`

## 3. Backup

```
2026-11-23 19:50:46,745 INFO     maintenance.backup: Backup 20261123T115046Z-2622bcd8 written to backups/20261123T115046Z-2622bcd8 (rows={'bookings': 3, 'system_state': 1}, sha256=2622bcd87933)
2026-11-23 19:50:46,750 INFO     maintenance.backup: Backup 20261123T115046Z-2622bcd8 loaded and verified (sha256=2622bcd87933)
```

- Backup identifier: `20261123T115046Z-2622bcd8`
- Creation time (UTC): `2026-11-23T11:50:46+00:00`
- Source instance identifier: `wispy-cake-58578289`
- Row counts: `{"bookings": 3, "system_state": 1}`
- Payload SHA-256: `2622bcd87933a430f379aedb0da8aab79728d8035bd533032f8e208004faade2`
- Storage location: `backups/20261123T115046Z-2622bcd8/`
  (`payload.json`, 1876 bytes, and `manifest.json`, 339 bytes, which records
  `backup_id`, `created_at`, `source_instance_id`, `app_version`,
  `row_counts`, `payload_sha256`, `schema_version`)

The backup is portable JSON with type tags, so it does not depend on `pg_dump`
or on the vendor. The same directory is reused when a run is resumed, which is
why the load-and-verify line appears again in section 7.

## 4. New instance and restoration

```
2026-11-23 19:54:36,765 INFO     maintenance.neon_provider: Created provider instance quiet-tree-18455871 (room-booking-prod-a90704c3)
2026-11-23 19:54:53,402 INFO     maintenance.backup: Restored backup 20261123T115046Z-2622bcd8 into postgresql+psycopg://neondb_owner:<redacted>@ep-curly-frost-bhpgqidx.c-10.eu-central-1.aws.neon.tech/neondb?sslmode=require (rows={'bookings': 3, 'system_state': 1})
2026-11-23 19:54:56,111 INFO     maintenance.backup: Verification of backup 20261123T115046Z-2622bcd8: content=True counts=True rule=True
2026-11-23 19:54:58,382 INFO     maintenance.controller: Identifier generation verified: new id 4 > restored max 3
2026-11-23 19:54:58,390 WARNING  maintenance.controller: Restore verified: {'booking_count': 3, 'overlap_pairs': [], 'rooms': ['Room A', 'Room B'], 'first_booking_id': 1}
```

- New instance identifier: `quiet-tree-18455871` (pg 17, `aws-eu-central-1`)
- Verification report: `content=True`, `counts=True`, `rule=True`
  (payload bytes, per-table row counts, and the domain-rule snapshot all match)
- Rule snapshot after restoration: `booking_count = 3`, `overlap_pairs = []`,
  `rooms = ["Room A", "Room B"]`, `first_booking_id = 1`
- Identifier generation: the first insert on the new database received id 4,
  which is greater than the restored maximum (3), so primary keys cannot
  collide with restored rows. The probe row was deleted immediately afterwards.

Independent check after the run, connecting directly to both projects:

| Project | Tables | Rows | Booking ids |
| --- | --- | --- | --- |
| `wispy-cake-58578289` (source) | `bookings`, `system_state` | 3 | 1, 2, 3 |
| `quiet-tree-18455871` (target) | `bookings`, `system_state` | 3 | 1, 2, 3 |

Both databases hold the same three reservations, including the adjacent
09:00-10:00 / 10:00-11:00 pair on `Room A` that the half-open interval rule
accepts.

## 5. Backend switch

```
2026-11-23 19:54:59,513 INFO     maintenance.render_client: Updated environment variable DATABASE_URL on Render service srv-dc1vhfqjnfac739trp50
2026-11-23 19:55:01,052 INFO     maintenance.render_client: Triggered Render deploy dep-dc22j568bjmc73b53hn0
2026-11-23 19:55:02,023 INFO     maintenance.render_client: Deploy dep-dc22j568bjmc73b53hn0 status=build_in_progress (0s elapsed)
2026-11-23 19:55:51,010 INFO     maintenance.render_client: Deploy dep-dc22j568bjmc73b53hn0 status=update_in_progress (45s elapsed)
2026-11-23 19:56:22,971 INFO     maintenance.render_client: Deploy dep-dc22j568bjmc73b53hn0 status=live (75s elapsed)
2026-11-23 19:56:22,972 WARNING  maintenance.controller: Deployment dep-dc22j568bjmc73b53hn0 finished with status live
2026-11-23 19:56:26,606 WARNING  maintenance.controller: Post-switch verification passed for instance quiet-tree-18455871
2026-11-23 19:56:28,617 WARNING  maintenance.controller: Maintenance mode released; application writes are open again
```

- Deployment identifier and final status: `dep-dc22j568bjmc73b53hn0` -> `live`
  after 75 s
- Render now holds `DATABASE_URL` with host
  `ep-curly-frost-bhpgqidx.c-10.eu-central-1.aws.neon.tech` (the **direct**
  host: the pooled `-pooler` suffix is stripped so psycopg3 prepared
  statements are not routed through pgbouncer)
- `/health` after the switch:
  `{"status":"ok","database":"ok","version":"1.0.0","maintenance_mode":false}`
- `GET /bookings?limit=500` -> `{"total": 3, ...}`, ids `1, 2, 3` - identical to
  the backup, so the cutover kept every reservation and its id
- Controlled probe write (`POST /admin/probe-write` with `X-Admin-Token`):
  `{"ok": true, "key": "probe:571edfc79314", "roundtrip": "written"}`
  (this endpoint is idempotent; it was called again after the run to capture the
  answer for this document, and the booking count stayed at 3)
- Maintenance mode released: `2026-11-23T11:56:28Z`,
  `GET /admin/maintenance` -> `{"maintenance_mode": false, ...}`

The Streamlit front end was not touched by the switch: it talks only to the
backend URL, so it followed the new database automatically
(`https://room-booking-tao.streamlit.app/`).

## 6. Normal replacement summary

```
{
  "run_id": "a90704c3ce72",
  "reason": "documented maintenance trial",
  "started_at": "2026-11-23T11:50:22+00:00",
  "status": "completed",
  "steps": [
    "writes_blocked",
    "source_instance_known",
    "backup_created",
    "instance_created",
    "restored",
    "restore_verified",
    "backend_switched",
    "content_verified",
    "probe_write_verified",
    "maintenance_released"
  ],
  "source_instance_id": "wispy-cake-58578289",
  "new_instance_id": "quiet-tree-18455871",
  "backup_id": "20261123T115046Z-2622bcd8",
  "backup_directory": "backups/20261123T115046Z-2622bcd8",
  "deploy_id": "dep-dc22j568bjmc73b53hn0",
  "message": "replacement completed"
}
```

Total duration: the run start is `11:50:22Z` and the last step is `11:56:28Z`
(about 6 min wall clock including the interrupted attempts below); the
successful pass from `11:54:34Z` to `11:56:28Z` took about 114 s, of which 75 s
was the Render build.

## 7. Failure, retry and resume (same run id)

The provider changed its project-creation contract while this trial was
prepared: `POST /projects` now rejects a personal API key unless the request
carries `org_id` **inside** the `project` object. Three attempts failed before
the creation step and each behaved as the assignment requires:

```
2026-11-23 19:50:47,972 ERROR    maintenance.controller: Replacement run a90704c3ce72 failed: GET /projects failed: HTTP 400 {"code":"","message":"org_id is required, ..."}
2026-11-23 19:50:49,137 WARNING  maintenance.controller: Maintenance mode kept active after the failure
2026-11-23 19:52:22,150 ERROR    maintenance.controller: Replacement run a90704c3ce72 failed: POST /projects?org_id=<redacted> failed: HTTP 400 {"code":"","message":"org_id is required, ..."}
2026-11-23 19:52:23,000 WARNING  maintenance.controller: Maintenance mode kept active after the failure
2026-11-23 19:52:58,478 ERROR    maintenance.controller: Replacement run a90704c3ce72 failed: POST /projects failed: HTTP 400 {"code":"","message":"org_id is required, ..."}
2026-11-23 19:52:59,301 WARNING  maintenance.controller: Maintenance mode kept active after the failure
2026-11-23 19:54:34,184 INFO     maintenance.backup: Backup 20261123T115046Z-2622bcd8 loaded and verified (sha256=2622bcd87933)
2026-11-23 19:54:34,184 WARNING  maintenance.controller: Reusing the verified backup 20261123T115046Z-2622bcd8 from the previous attempt
```

What this demonstrates:

- writes stayed blocked across every failure (the flag lives in the database,
  so it survived the restart of the controller);
- the run did not guess: a rejected create stopped it instead of retrying
  blindly, and no project was created while the outcome was unknown;
- the fourth attempt **resumed** the same run id, reloaded the already
  verified backup instead of taking a new one, and completed the remaining
  steps;
- during API investigation one throwaway Neon project was created and deleted
  again to confirm the accepted request shape; it never became the target.

The fix is in `maintenance/neon_provider.py`:
`NeonProvider._organisation()` resolves the organisation once
(`GET /users/me/organizations`, or the configured `NEON_ORG_ID`), the project
listing is called with `?org_id=...`, and the create body carries
`"project": {..., "org_id": ...}`. A unit test asserts the outgoing body.

## 8. Evidence review checklist

- [x] every pasted line comes from run id `a90704c3ce72`
- [x] no API key, password or full connection string remains
      (`npg_...` is shown as `<redacted>`, the Render token is never printed)
- [x] the maintenance flag was enabled before the backup and released only
      after the post-switch verification
      (`writes_blocked` -> ... -> `maintenance_released`)
- [x] the backup is reproducible from the recorded inputs: the manifest and the
      run state are committed under `docs/trial-artifacts/` (`manifest.json`,
      `run-state.json`, and the trimmed `maintenance-run.log`); the payload
      itself (`backups/20261123T115046Z-2622bcd8/payload.json`, 1876 bytes) lives
      in the run workspace, and its SHA-256 is recorded above
- [x] the run state shows `completed`, not `in_progress`

The source project `wispy-cake-58578289` was **not** deleted: the trial ran
without `--delete-source`, so the previous database is still available as a
rollback target. Deleting it requires the explicit flag, which is why it is a
separate, deliberate decision.
