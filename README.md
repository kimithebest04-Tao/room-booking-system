# Room Booking Service

Multi-component Python application for booking meeting rooms, built for the
*Multi-Paradigm Programming Languages* practical course (2026/2027, semester 1).

| Component | Technology | Location |
| --- | --- | --- |
| REST API | FastAPI + Pydantic | `backend/` |
| Web interface | Streamlit | `frontend/app.py` |
| Persistence | SQLAlchemy ORM + PostgreSQL (Neon) | `backend/models.py`, `backend/db.py` |
| Maintenance controller | asyncio + APScheduler + httpx | `maintenance/` |
| Test suite | pytest | `tests/` |
| CI | GitHub Actions | `.github/workflows/ci.yml` |

Cloud links:

- Streamlit app: <https://room-booking-tao.streamlit.app/>
- FastAPI backend: <https://room-booking-api-0khx.onrender.com/docs>
- GitHub repository: <https://github.com/kimithebest04-Tao/room-booking-system>
- CI runs: <https://github.com/kimithebest04-Tao/room-booking-system/actions>

---

## 1. Application

### 1.1 Purpose

The system reserves meeting rooms. A reservation is accepted only when the
requested slot does not overlap another reservation of the **same room**; the
boundary case (one reservation ends exactly when the next one starts) is
allowed. The interface shows the reservations, the per-room utilization and the
result of every failed request.

### 1.2 Domain-specific rule

The rule is implemented once, in `backend/processing.py::intervals_overlap`:

```python
return start_a < end_b and start_b < end_a   # half-open intervals [start, end)
```

| Input | Output |
| --- | --- |
| 09:00–10:00 vs 10:00–11:00 | no conflict (back-to-back, `<` not `<=`) |
| 09:00–10:00 vs 09:30–10:30 | conflict |
| 09:00–10:00 vs 08:00–09:00 | no conflict (back-to-back, other direction) |
| 09:00–10:00 vs 08:00–11:00 | conflict (containment) |
| 10:00–09:00 (end before start) | invalid input, HTTP 422, never "free" |

- **Inputs**: room name, start, end, booker, optional purpose.
- **Output**: a stored reservation or a `409 booking_conflict` error that names
  the conflicting reservation ids.
- **Conditions**: the interval must satisfy `start < end`; only reservations of
  the same room are compared; room names are whitespace-normalized.
- **Limitations**: there is no travel/buffer time between reservations and no
  cross-room rules — deliberately, to keep the rule pure and testable. The
  overlap check is a read-then-write sequence, so two simultaneous requests for
  the same slot could both pass; a production system would add a database level
  constraint or an application lock.
- **Where it is used**: `BookingService.create` (HTTP 409 for the user) and the
  utilization calculation, which reuses the same interval helpers.

### 1.3 Features

- Create a reservation with validation and conflict detection (`POST /bookings`).
- Filter reservations by room and date range (`GET /bookings`).
- Per-room utilization for a date window (`GET /stats/utilization`).
- Maintenance mode that blocks writes in the **database**, not in the UI.
- Authenticated probe write used to verify a freshly restored database.
- Scheduled database status checks and an authorised instance-replacement
  workflow with backup, restoration, verification and backend switch.

### 1.4 Data sources

All data is synthetic. `scripts/seed.py` generates a fixed set of ten
reservations across three rooms and three days (including three back-to-back
boundary pairs) using fictional names (`Alice Example`, `Bruno Sample`, ...).
No real personal data is used, and the published test backup therefore contains
none either. The database starts empty on a fresh deployment; the seed script or
the interface can fill it.

### 1.5 Data flow

The mandatory flow is implemented end to end:

```
Streamlit form  →  POST /bookings          (frontend/app.py, api_request)
                →  FastAPI router          (backend/api/bookings.py)
                →  BookingService.create   (backend/service.py, OOP layer)
                →  intervals_overlap + find_overlapping   (backend/processing.py, pure functions)
                →  SQLAlchemy ORM insert   (backend/models.py)
                →  201 + JSON body         (Pydantic BookingRead)
```

The frontend only ever speaks HTTP to `API_BASE_URL`; it imports no SQLAlchemy
model and opens no database connection.

### 1.6 Programming paradigms

| Paradigm | Where | Why it is genuine |
| --- | --- | --- |
| **Procedural** | `maintenance/controller.py` — `replace_instance` orchestrates `_step_block_writes`, `_step_backup`, `_step_create_instance`, `_step_restore`, `_step_switch`, `_step_verify`, `_step_release` as an explicit, ordered workflow of functions with a saved run state. `maintenance/backup.py` is a second example (`create_backup`, `restore_backup`, `verify_restore`). | Each function does one step, reports its result through a dataclass and can be re-run. |
| **Functional** | `backend/processing.py` — `intervals_overlap`, `find_overlapping`, `overlap_minutes`, `open_windows`, `calculate_utilization`, `group_intervals`, `normalize_text` | Same input → same output, no I/O, no mutation of arguments, frozen `Interval`/`RoomUsage` dataclasses. Verified by `test_find_overlapping_reports_conflicts_and_leaves_input_unchanged`. |
| **Object-oriented** | `backend/service.py::BookingService` (conflict refusal, maintenance flag, utilization query, probe write); `maintenance/provider.py::DatabaseProvider` with `NeonProvider`; `maintenance/backup.py` dataclasses `BackupManifest`, `VerificationReport` | `BookingService` owns the unit of work and the business behaviour; the provider class hierarchy isolates the cloud API behind an interface, which is what makes the tests injectable. Pydantic/ORM models alone would not satisfy this requirement. |

### 1.7 Repository layout

```
backend/
  main.py        FastAPI factory, logging, error handlers, /health
  api/           bookings.py, stats.py, admin.py  (HTTP layer only)
  service.py     BookingService (OOP)
  processing.py  pure domain functions
  models.py      SQLAlchemy ORM (bookings, system_state)
  schemas.py     Pydantic request/response models
  config.py      environment-driven Settings
  db.py          engine + session lifecycle
frontend/app.py  Streamlit interface (HTTP only)
maintenance/
  controller.py  scheduled checks + replacement workflow
  provider.py    DatabaseProvider interface, retry helper
  neon_provider.py  Neon REST API client
  render_client.py  Render env-var/deploy API client
  backend_client.py async client for the backend admin API
  backup.py      portable export/import + verification
  run.py         CLI entry point
tests/           conftest.py, test_domain_rule.py, test_service.py,
                 test_maintenance.py, test_api_integration.py
scripts/seed.py  synthetic demo data
docs/            maintenance trial and bug-fix evidence templates
start.py         starts backend + frontend + scheduled checks
```

---

## 2. Usage

### 2.1 Python version and dependencies

Developed and verified locally on **Python 3.14.0** (the venv that produced the
passing run reported in section 4.1). CI and the cloud deployment target
**Python 3.13**, which is the version pinned in `.github/workflows/ci.yml`.
`requirements.txt` pins every dependency:

```
fastapi==0.165.0            uvicorn[standard]==0.54.0
SQLAlchemy==2.1.9           psycopg[binary]==3.3.9
pydantic==2.14.4            pydantic-settings==2.16.3
httpx==0.28.1               APScheduler==3.11.3
python-dotenv==1.2.4        streamlit==1.67.0
pandas==3.1.1               altair==6.3.0
pytest==9.2.0               pytest-asyncio==1.5.0
```

### 2.2 Installation

```bash
python -m venv venv
# Windows: .\venv\Scripts\activate      Linux/macOS: source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

No external database client is required: the backup is produced by Python
(SQLAlchemy export), so `pg_dump` does not have to be installed. The PostgreSQL
driver `psycopg[binary]==3.3.9` is pinned in `requirements.txt`; the deployed
`DATABASE_URL` uses the `postgresql+psycopg://` scheme.

### 2.3 Configuration

Copy `.env.example` to `.env` and adjust. `.env` is git-ignored.

| Variable | Meaning | Local value |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///./data/app.db` |
| `ADMIN_TOKEN` | token required by every `/admin` endpoint | long random string |
| `TIMEZONE` | zone for storing/reading naive timestamps | `Europe/Budapest` |
| `OPEN_HOUR` / `CLOSE_HOUR` | opening hours used as utilization denominator | `8` / `20` |
| `NEON_API_KEY` | provider API key (maintenance controller only) | secret |
| `NEON_PROJECT_ID` | current instance identifier | secret-ish |
| `NEON_ORG_ID` | organisation the project is created in (the provider now requires it; resolved from the API key when left empty) | `org-...` |
| `RENDER_API_KEY` / `RENDER_SERVICE_ID` | deployment control | secret |
| `BACKEND_BASE_URL` | API base URL the controller calls | `http://127.0.0.1:8000` |
| `API_BASE_URL` | API base URL the frontend calls | `http://127.0.0.1:8000` |
| `MAINTENANCE_INTERVAL_MINUTES` | status check period | `60` |
| `MAINTENANCE_AUTO_REPLACE` | authorise scheduled replacement | `false` |

Secrets must never appear in the interface, the logs or Git; the logs below were
checked for that before submission.

### 2.4 Database initialisation

The schema is created on backend startup (`AUTO_CREATE_SCHEMA=true`, default) or
explicitly:

```bash
python scripts/seed.py --date 2026-12-02    # creates tables and the demo data
```

### 2.5 Running locally

From the repository root, in separate terminals:

```bash
uvicorn backend.main:app --reload                 # http://127.0.0.1:8000/docs
streamlit run frontend/app.py                     # http://127.0.0.1:8501
python -m maintenance.run watch                   # scheduled status checks
python -m pytest                                  # shared test suite
```

Or start everything with one command:

```bash
python start.py                                   # backend + frontend + checks
python start.py --no-frontend                     # backend + checks only
```

`start.py` waits for `/health` before starting Streamlit, restarts nothing and
never deletes or replaces a database. Ctrl+C stops all child processes.

Shutdown: Ctrl+C in the terminal(s); `start.py` terminates its children.

### 2.6 API endpoints

| Method & route | Purpose | Success | Documented failures |
| --- | --- | --- | --- |
| `POST /bookings` | create a reservation | 201 + `BookingRead` | 409 `booking_conflict` (with conflicting ids), 422 invalid interval, 503 writes blocked |
| `GET /bookings?room=&date_from=&date_to=&limit=` | list reservations | 200 + `{total, items}` | 422 invalid query type |
| `GET /stats/utilization?date_from=&date_to=` | per-room utilization | 200 + window, rooms, generated_at | 422 reversed window |
| `GET /rooms` | room names for suggestions | 200 | — |
| `GET /health` | liveness + DB check | 200 `{status, database, version, maintenance_mode}` | — |
| `POST /admin/maintenance` | enable/disable the write block (`X-Admin-Token`) | 200 | 401 |
| `GET /admin/maintenance` | read the flag (`X-Admin-Token`) | 200 | 401 |
| `POST /admin/probe-write` | controlled write test (`X-Admin-Token`) | 200 `{ok, key, roundtrip}` | 401 |

Example:

```bash
curl -X POST http://127.0.0.1:8000/bookings -H "Content-Type: application/json" -d '{
  "room": "Room A", "booker": "Alice Example",
  "start_at": "2026-12-02T09:00:00", "end_at": "2026-12-02T10:00:00"}'
```

Timestamps are stored as naive local time in `TIMEZONE`; offsets in the request
are converted, never silently truncated. Timestamps without an offset are taken
as already local.

### 2.7 Deployment (cloud)

1. **Neon** — create a free PostgreSQL project in the EU region, copy its
   connection string, and paste it as `DATABASE_URL` of the backend.
2. **Render** — create a Web Service from this repository with
   `pip install -r requirements.txt` as build command and
   `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` as start command.
   Set `DATABASE_URL`, `ADMIN_TOKEN`, `AUTO_CREATE_SCHEMA=true` in the
   environment tab (never in Git).
3. **Streamlit Community Cloud** — deploy `frontend/app.py` from the same
   repository and set `API_BASE_URL` to the Render URL in the app secrets.
4. Verify: `/docs` answers, the Streamlit app creates a reservation and the
   utilization chart shows it after a reload.

A raw `postgresql://` string copied from the provider console is accepted as
well: the engine rewrites it to the `postgresql+psycopg://` scheme, so the
pinned psycopg 3 driver is always used.

Operational limitations that follow from the free tiers (section 5.3 of the
brief): Render's free web service has a non-persistent filesystem and may spin
down when idle (the first request after a pause is slow); the free PostgreSQL
instance is suspended after a period of inactivity and only one instance can be
active at a time, so the replacement workflow must restore into the new instance
before the old one is deleted. Startup delays and maintenance downtime are
expected.

---

## 3. Maintenance

### 3.1 Controller startup and scheduling

```bash
python -m maintenance.run check      # one status report (JSON on stdout)
python -m maintenance.run watch      # APScheduler interval job, Ctrl+C to stop
python -m maintenance.run state      # persisted run state
```

The interval job uses `AsyncIOScheduler` and an `async def` check that performs
a real `await`ed provider API call through `httpx.AsyncClient`
(`maintenance/neon_provider.py::_request`). The scheduler is created once per
process; `start_scheduler()` refuses to start a second one, so a restart does
not silently add another schedule.

### 3.2 Authorisation and maintenance mode

- Every `/admin` route requires `X-Admin-Token`; without it the API answers 401.
  The controller is a local CLI, not a public endpoint.
- Replacement requires an explicit `--confirm` flag. Scheduled checks only
  replace when `MAINTENANCE_AUTO_REPLACE=true` **and** the backend is reachable
  **and** the database holds application data **and** the expiry is inside the
  warning window. A connection failure or an empty table never triggers it.
- While replacement is running, writes are refused by the **database** flag
  (`system_state.maintenance_mode`): `POST /bookings` answers 503. Hiding a
  button in the UI would not be enough, and the flag is copied by the backup so
  the freshly restored instance stays blocked until verification passes.

### 3.3 Replacement workflow

Implemented in `maintenance/controller.py::replace_instance` (steps map 1:1 to
section 6.3 of the brief):

1. **Prerequisites and write block** — verify the provider instance is ready, the
   backend is healthy, then enable and read back the maintenance flag; wait 2 s
   for in-flight writes to drain (`_step_block_writes`).
2. **Final backup** — export the current state (including records added after the
   initial data load), check the checksum immediately, store it in `backups/`
   with its creation time and source instance id (`_step_backup`,
   `maintenance/backup.py::create_backup`).
3. **New instance** — create the instance through the provider API with a name
   derived from the run id. On any interruption the instance is found again by
   that name (or by the identifier stored in the run state), so a resumed run
   never creates a second one (`_step_create_instance`).
4. **Restore and verify** — refuse a non-empty target, restore schema, rows and
   identifier generation, compare the SHA-256 of the re-export with the backup,
   compare row counts and the rule snapshot (overlapping pairs recomputed from
   the restored rows), then insert-and-delete a probe booking to prove id safety
   (`_step_restore`, `restore_backup`, `verify_restore`, `check_identifier_generation`).
5. **Switch the backend** — update `DATABASE_URL` through the Render API, trigger
   a deployment and poll it until `live` (`_step_switch`, `render_client.py`).
6. **Verify through the API** — wait for `/health`, compare the reservation count
   reported by `GET /bookings` with the backup, confirm the maintenance flag is
   still on, then perform the controlled probe write (`_step_verify`).
7. **Release** — leave maintenance mode only after all checks pass
   (`_step_release`). Deleting the old instance is a separate, explicitly
   requested step (`--delete-source`).

### 3.4 Partial failures and safe recovery

- **Bounded retries**: every provider call has a timeout and at most
  `HTTP_MAX_RETRIES` attempts; only transient failures (network, 429, 5xx) are
  retried. Authentication and quota errors stop immediately and never upgrade a
  plan (`maintenance/provider.py::with_retries`).
- **Uncertain creation**: if creation times out, the controller lists instances
  and adopts the one with the expected name; if none exists it stops with a clear
  error instead of guessing (`_step_create_instance`).
- **Failed restore or switch**: maintenance mode is re-asserted, the backup and
  the run state are preserved, and the error names the failed step
  (`replace_instance` failure path, tested by
  `test_failed_restore_keeps_maintenance_mode_active`).
- **Interrupted run**: `maintenance_state/run_state.json` records completed
  steps, the new instance id, the deploy id and the backup path; `resume`
  continues from the first unfinished step (tested by
  `test_interrupted_run_resumes_without_a_second_instance_or_backup`).
- **Resource protection**: the controller acts only on the configured source
  instance and the instance it created; deletion happens only with
  `--delete-source` after a successful verification.

### 3.5 Backup format and storage

- Portable JSON (`payload.json`) holding `{"tables": {name: {columns, rows}}}`,
  plus `manifest.json` with `backup_id`, `created_at` (UTC), `source_instance_id`,
  `app_version`, per-table row counts and the payload SHA-256.
- Values are type-tagged (`{"__type__": "datetime", "value": "..."}`) so
  datetimes/dates/decimals survive the round trip; ids are restored explicitly
  and the identity sequence is reset to `max(id)`.
- Location: `backups/<backup_id>/` — a local durable directory independent of the
  source database and the backend process, git-ignored.
- Integrity: `load_backup` rejects missing files, missing manifest fields and
  checksum mismatches, so a damaged backup is never restored silently.

### 3.6 Logs and the documented trial

- Backend: `logs/app.log` + stdout (`component` = logger name, e.g.
  `backend.api.bookings`, `backend.service`).
- Controller: `logs/maintenance.log` + stdout (`maintenance.controller`,
  `maintenance.neon_provider`, ...).
- Run state: `maintenance_state/run_state.json`.

The complete replacement trial (one run, with the status check, backup,
restoration, switch and verification in the same log file) is documented in
[`docs/maintenance-trial.md`](docs/maintenance-trial.md), together with the
timestamped backup manifest and the verification output. Secrets are removed
from every pasted line.

---

## 4. Verification

### 4.1 Test suite

```bash
python -m pytest -q
```

31 tests, five test functions per the brief's minimum of three unit + one
integration:

| File | Test | Covers |
| --- | --- | --- |
| `tests/test_domain_rule.py` | `test_overlap_rule_matches_the_hand_computed_table` (parametrized, 7 cases) | normal, boundary, containment, disjoint slots of the rule |
| | `test_invalid_intervals_are_rejected` (parametrized) | end before start / empty interval |
| | `test_find_overlapping_reports_conflicts_and_leaves_input_unchanged` | purity: input not mutated, conflicts returned |
| | `test_overlap_minutes_clips_and_never_returns_negative_values` | clipping arithmetic |
| | `test_open_windows_are_one_per_calendar_day` | window generation, invalid range |
| | `test_utilization_uses_opening_hours_as_the_denominator` | denominator = open minutes |
| | `test_utilization_ignores_time_outside_opening_hours` | night/over-close clipping |
| `tests/test_service.py` | `test_service_rejects_overlap_and_allows_back_to_back` | service-level 409 vs 201, other room |
| | `test_service_blocks_writes_while_maintenance_mode_is_enabled` | maintenance flag enforcement |
| | `test_service_reports_utilization_for_the_requested_window` | OOP layer + pure layer together |
| | `test_request_validation_rejects_unusable_intervals` (parametrized) | Pydantic rejects invalid intervals |
| `tests/test_maintenance.py` | `test_backup_restore_preserves_content_types_and_identifier_generation` | backup → restore → same digest, same rule, safe next id |
| | `test_restore_refuses_a_database_that_is_not_empty` | blank-target requirement |
| | `test_missing_or_corrupted_backup_is_rejected` | missing and tampered backup |
| | `test_status_check_never_replaces_an_empty_database` | empty table does not justify replacement |
| | `test_temporary_provider_failure_stops_without_creating_an_instance` | bounded retries, no guessed instance |
| | `test_interrupted_run_resumes_without_a_second_instance_or_backup` | resume semantics |
| | `test_failed_restore_keeps_maintenance_mode_active` | safe stop, backup preserved |
| `tests/test_provider_clients.py` | `test_render_env_var_update_uses_single_variable_endpoint` | the switch calls the right Render endpoint |
| | `test_render_env_var_update_merges_instead_of_wiping_other_values` | a failing single-variable route keeps every other variable |
| | `test_neon_create_instance_returns_a_ready_connection_string` | instance creation returns a usable connection string |
| | `test_provider_connection_strings_use_the_installed_postgres_driver` | a raw `postgresql://` string is routed to psycopg 3 |
| `tests/test_api_integration.py` | `test_booking_api_end_to_end` | real FastAPI + real SQLAlchemy on an isolated file DB: 201 → GET → 409 → boundary 201 → 422 → stats → 401/503 maintenance block → 200 release |

The suite uses only temporary SQLite files (`tests/conftest.py`), simulated
provider responses (`FakeProvider`, `FakeDeployer`, `FakeBackend`) and never
touches a live service, the deployed database or a secret.

Verification result of the submitted version:

```
$ python -m pytest -q
...............................                                          [100%]
31 passed
```

### 4.2 CI and the documented bug fix

`.github/workflows/ci.yml` installs the pinned requirements and runs
`python -m pytest -q` on every push and pull request (one runner, Python 3.13).

The debugging example required by section 4.4 is recorded in
[`docs/bug-fix-demo.md`](docs/bug-fix-demo.md): a fault was introduced on a
non-deployed branch, CI failed, the fault was fixed and CI passed. Links to both
runs are there. Skipping or deleting a failing test is not used as a fix.

---

## 5. Sources, AI use and extensions

### 5.1 AI tool use

AI assistance (a coding assistant inside the IDE chat) was used for:

- drafting the initial project skeleton (package layout, SQLAlchemy models,
  Pydantic schemas) and the first version of this README,
- proposing the parametrized test table for the overlap rule,
- reviewing error messages and HTTP status mapping.

How the output was checked: every generated file was read, executed locally
(`uvicorn`, `streamlit`, `pytest`, `scripts/seed.py`) and the observed behaviour
was compared with the brief. The failing cases found during review (utilization
rounding, seed data not producing a real boundary pair) were corrected and the
suite re-run. All code in the repository is understood and can be explained
during the presentation; no password, API key or personal data was shared with
any tool.

### 5.2 Reused code and data

- No third-party project code was copied. The only reused material is public
  documentation: the FastAPI, SQLAlchemy, Streamlit, APScheduler and Neon API
  docs.
- All data is synthetic, produced by `scripts/seed.py`; no external dataset and
  no personal data is included.

### 5.3 Extensions

None of the optional extensions (threading, scraping, e-mail, Docker) is
implemented. The scope was deliberately kept minimal so that the mandatory
components, the rule and the maintenance workflow are complete and verifiable
rather than partially finished.

---

## 6. Design decisions

**1. Half-open intervals, adjacency allowed.** The rule treats a reservation as
`[start, end)`, so 09:00–10:00 and 10:00–11:00 coexist. *Alternative considered:*
a fixed buffer (for example 15 minutes) between reservations. *Chosen:* the plain
half-open rule, because it is the behaviour users expect from a room calendar,
matches the brief's example and keeps the function a single boolean expression
that the tests can verify by hand. *Accepted limitation:* no cleaning/travel
time, which a real facility system would add as a second rule.

**2. Utilization measured against opening hours.** The denominator is
`OPEN_HOUR..CLOSE_HOUR` on each day of the window (8:00–20:00 by default), and
reservations are clipped to it. *Alternative:* utilization against 24 hours.
*Chosen:* opening hours, otherwise a night-time booking would look like a tiny
percentage and rooms would show misleadingly low numbers. *Accepted limitation:*
the value depends on the configured hours, which is documented and configurable.

**3. Maintenance mode stored in the database, portable JSON backup.** The write
block is a row in `system_state`, not an in-memory flag or a UI setting, and the
backup is a type-tagged JSON export produced by Python. *Alternatives:* a Render
environment variable for the flag, and `pg_dump` for the backup. *Chosen:* the
database flag survives the switch (it is copied by the backup, so the restored
instance is still blocked until verification) and the JSON export needs no
external binary, keeps types and can be inspected in the repository. *Accepted
limitation:* the format is specific to this schema, and a very large database
would need a streaming exporter.

**4. Idempotent run state instead of a one-shot script.** Every step records its
completion in `maintenance_state/run_state.json` and the target instance name is
derived from the run id, so a crash or a restart resumes instead of duplicating
instances or backups. *Alternative:* restart the whole workflow on failure.
*Chosen:* re-running must not create a second instance or duplicate data
(section 6.4), and this is exactly what the resume test checks. *Accepted
limitation:* only one replacement may run at a time; parallel runs are refused.

---

## 7. Known limitations

- No authentication for the public booking endpoints (not required by the brief);
  only the maintenance controls are protected.
- The overlap check is read-then-write, so concurrent identical requests could
  both succeed; the rule itself is correct, the race is a deployment-level
  concern.
- Timestamps are naive local time in `TIMEZONE`; multi-time-zone use would need
  `timestamptz` and a display conversion.
- The maintenance controller runs locally (as allowed by the brief); it is not a
  cloud service, so it must be running for scheduled checks to happen.
