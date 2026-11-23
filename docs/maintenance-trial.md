# Documented cloud replacement trial

Everything in this file must come from **one** replacement run. The logs below
are the trimmed output of `logs/maintenance.log` for that run; the run state is
`maintenance_state/run_state.json`. Secrets (API keys, connection strings with
passwords) are replaced with `<redacted>` in every pasted line.

## 1. How the run was performed

```bash
# terminal 1 - the application under maintenance
python start.py

# terminal 2 - the authorised replacement
python -m maintenance.run check            # status, expiry information
python -m maintenance.run replace --reason "planned maintenance" --confirm
python -m maintenance.run state            # persisted run state afterwards
```

The run must be authorised beforehand (`--confirm`). `watch` alone never
replaces anything.

## 2. Scheduled asynchronous status check

```
<paste: the "Status check: {...}" line and the provider API lines from logs/maintenance.log>
```

- Instance identifier: `<paste>`
- Expiry information reported by the provider: `<paste>`
- Backend health and reservation count at check time: `<paste>`

## 3. Backup

```
<paste: "Backup <id> written to backups/<id> (rows=..., sha256=...)" and "Backup <id> loaded and verified">
```

- Backup identifier: `<paste>`
- Creation time (UTC): `<paste>`
- Source instance identifier: `<paste>`
- Row counts: `<paste>`
- Payload SHA-256: `<paste>`
- Storage location: `backups/<backup_id>/` (`payload.json` + `manifest.json`)

## 4. New instance and restoration

```
<paste: "Created provider instance ...", "Restored backup ... into <redacted>", "Verification of backup ... content=True counts=True rule=True", "Identifier generation verified: new id ... > restored max ...">
```

- New instance identifier: `<paste>`
- Verification report (`content_matches`, `row_counts_match`, `rule_matches`): `<paste>`
- Rule snapshot after restoration (overlap pairs and booking count): `<paste>`

## 5. Backend switch

```
<paste: "Updated environment variable DATABASE_URL on Render service ...", "Triggered Render deploy ...", "Deploy ... status=live">
```

- Deployment identifier and final status: `<paste>`
- `/health` answer after the switch: `<paste>`
- `GET /bookings` total compared with the backup: `<paste>`
- Controlled probe write result: `<paste: {"ok": true, ...}>`
- Maintenance mode released: `<paste>`

## 6. Normal replacement summary

```
<paste: the final run_state.json with status "completed" and "message": "replacement completed">
```

Total duration of the run: `<paste, optional>`.

## 7. Evidence review checklist

- [ ] every pasted line comes from the same run id
- [ ] no API key, password or full connection string remains
- [ ] the maintenance flag was enabled before the backup and released only after
      the post-switch verification
- [ ] the backup directory referenced above still exists at submission time
- [ ] the run state shows `completed`, not `in_progress`
