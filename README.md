# FHIR R4 Patient Migration

A migration tool and viewer for patient records from a FHIR R4 API (HAPI FHIR sandbox) into an internal service with a simplified data model.

## What this does

1. **Migrates** patient records and observations from the [HAPI FHIR R4 sandbox](https://hapi.fhir.org/baseR4) into a local SQLite database
2. **Exposes** a REST API for browsing migrated patients and their observations
3. **Provides** a Vue frontend for viewing patients and their clinical observations

## Architecture

```
FHIR R4 API → Fetch (retry/backoff) → Transform (flatten JSON) → Persist (bulk upsert) → REST API → Vue Frontend
```

| Layer | Technology |
|-------|-----------|
| Backend | Django 6.1 + Django REST Framework |
| Database | SQLite (no server needed) |
| Frontend | Vue 3 + Vite + Tailwind CSS v4 + shadcn-vue |
| FHIR Client | Python `requests` with pagination and exponential backoff |

Patients and observations are fetched with **two separate paginated searches**
(`/Patient`, then `/Observation?subject=…` in batches) rather than a single
`_revinclude=Observation:patient` query. `_revinclude` needs fewer requests, but HAPI
caps included resources at 1000 per page and drops the rest silently — at a page size
of 100 that loses roughly 90% of observations with no error. See
[REVIEW.md](REVIEW.md) for the measurements.

## Setup

### Prerequisites

- Python 3.12+ (tested on 3.14)
- Node.js 20+ (tested on 22)

No credentials, API keys, or services to configure — it runs as-is.

### Terminal 1 — backend

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
python manage.py migrate

python manage.py migrate_fhir      # ~2.5 min against the live sandbox
python manage.py runserver         # leave running
```

`migrate_fhir` pulls ~7,000 patients and ~38,000 observations. It prints a line per
page as it goes, so if it looks idle for more than a few seconds something is wrong —
it isn't silent. It ends with a validation summary and exits non-zero on failure.

In a hurry? `python manage.py migrate_fhir --max-pages 5` gives you a few hundred
patients in seconds. Note that a partial run is *deliberately* left resumable, so the
patient list will show an amber "last migration run is running at patient offset N"
banner. That banner is the feature working, not a bug — it exists so an incomplete
migration is never presented as the full dataset. Re-run without `--max-pages` to
finish (it resumes), or `--restart` to start clean.

### Terminal 2 — frontend

`runserver` occupies the first terminal, so open a second one. The Vite dev server
proxies `/api` to `127.0.0.1:8000`, so the backend needs to be running.

```bash
cd frontend
npm install
npm run dev
```

- Frontend: <http://localhost:5173>
- API: <http://localhost:8000/api/>

### If the sandbox is having a bad day

`https://hapi.fhir.org/baseR4` is a shared public server that is periodically reset.
If it is down, empty, or throttling, `migrate_fhir` will retry transient failures and
then abort with a non-zero exit and a message naming the cause — it will not write
half a dataset and claim success. Re-run it later; the migration is resumable and
idempotent, so nothing is lost. To point at a different server:
`FHIR_BASE_URL=https://your-server/baseR4 python manage.py migrate_fhir`.

### Admin Interface

Create an admin user to access the Django admin panel:

```bash
python manage.py createsuperuser
```

Visit `http://localhost:8000/admin/` to browse and manage migrated data.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/patients/` | Paginated list of patients (with observation count) |
| `GET /api/patients/{fhir_id}/` | Patient detail with nested observations |
| `GET /api/observations/?patient={id}` | Observations filtered by patient |
| `GET /api/migration-status/` | Patient/observation counts plus the latest run's status, checkpoint offset and error |

## Running Tests

```bash
source .venv/bin/activate
python manage.py test api -v2
```

46 tests covering:
- FHIR-to-internal transforms (field mapping, reference forms, `effective[x]` fallbacks, decimal precision, missing fields)
- The FHIR client's failure handling (retry/backoff, `Retry-After`, timeouts, fail-fast on 4xx, pagination, observation batching)
- The migration command end to end against a stubbed client (mapping, idempotency, skipped resources, validation output, non-zero exit on upstream failure)
- Checkpointing (checkpoint advances per page, survives a failure, resumes at the right offset, `--restart` overrides it, a finished run isn't resumed)
- API endpoints (list, detail, 404, filtering, numeric serialisation)

## Migration Options

```bash
python manage.py migrate_fhir [options]

  --page-size N      Patients per FHIR API page (default: FHIR_PAGE_SIZE, 100)
  --max-pages N      Stop after N patient pages (for testing)
  --delay SECONDS    Delay between API requests (default: 0.1)
  --restart          Ignore any unfinished run and start from the first page
```

The migration is **idempotent** — every write is an upsert keyed on the FHIR resource
id, so re-running updates existing records rather than duplicating them.

It is also **resumable**. Each run is tracked in a `MigrationRun` row, checkpointed
after every page. If the process dies, just run the same command again:

```
$ python manage.py migrate_fhir
Resuming the running run started 2026-08-23 16:17:50 at patient offset 1100
  Page 1 (offset 1125): 25 patients read | cumulative 1125 patients, 5765 observations written
```

The checkpoint advances only after a page's patients *and* their observations are
committed, so a crash mid-page replays that page rather than skipping it — safe,
because the writes are upserts. `--max-pages` also leaves a resumable checkpoint, so
you can walk the migration forward in chunks.

The command exits non-zero if it aborts, and prints validation checks on completion:
counts compared against the server's own totals, data-quality counters, and a check
that flags any field which is NULL on every row (the signature of a broken mapping
rather than sparse source data). Validation queries the database rather than
in-process counters — the counters say what we think happened, the database says what
did.

## Configuration

Everything runs with no configuration. To override:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FHIR_BASE_URL` | `https://hapi.fhir.org/baseR4` | Source FHIR server — **synthetic data only** |
| `FHIR_PAGE_SIZE` | `100` | Patients per page |
| `FHIR_OBSERVATION_PAGE_SIZE` | `200` | Observations per page |
| `FHIR_MAX_RETRIES` | `3` | Attempts per request |
| `FHIR_REQUEST_TIMEOUT` | `30` | Per-request timeout (seconds) |
| `MIGRATION_LOG_LEVEL` | `INFO` | Log level for the `api` logger |
| `DJANGO_SECRET_KEY` | insecure dev fallback | Must be set outside local dev |
| `DJANGO_DEBUG` | `True` | Set `False` outside local dev |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated |

## Project Structure

```
.
├── Plan.md                          # Migration plan (Part 1)
├── REVIEW.md                        # Review findings + what changed as a result
├── manage.py                        # Django management script
├── requirements.txt                 # Python dependencies
├── fhir_migration/                  # Django project settings
│   ├── settings.py
│   └── urls.py
├── api/                             # Django app
│   ├── models.py                    # Patient + Observation + MigrationRun (checkpoint)
│   ├── serializers.py               # DRF serializers
│   ├── views.py                     # DRF viewsets + endpoints
│   ├── admin.py                     # Django admin registration
│   ├── tests.py                     # Backend tests
│   ├── fhir/                        # FHIR integration
│   │   ├── client.py                # FHIR API client (pagination, retry)
│   │   └── transforms.py            # FHIR JSON → internal model
│   └── management/commands/
│       └── migrate_fhir.py          # Migration CLI command
└── frontend/                        # Vue 3 frontend
    ├── src/
    │   ├── views/
    │   │   ├── PatientList.vue      # Patient list with pagination
    │   │   └── PatientDetail.vue    # Patient detail with observations
    │   ├── components/ui/           # shadcn-vue components
    │   ├── composables/             # Data fetching + state (views stay presentational)
    │   ├── api/index.js             # API client
    │   ├── lib/format.js            # Shared date/value/label formatters
    │   └── router/index.js          # Vue Router
    └── vite.config.js
```

## Time Spent

Tracked against the suggested 3-hour cap:

| Phase | Time |
|-------|------|
| Part 1 — sandbox exploration + Plan.md, including review and edits | 36 min |
| Part 2 — initial working slice (models, client, transforms, command, API, frontend) | ~17 min |
| Part 2 — review and hardening pass (see [REVIEW.md](REVIEW.md)) | ~75 min |
| Frontend composables refactor | ~15 min |
| Setup rehearsal from a clean clone, and the bug it found | ~30 min |
| **Total** | **≈ 2 h 55 min** |

The hardening pass is the largest block and was not polish: it is where the
`_revinclude` data loss, the `transaction.atomic()` misuse, and the exit-code bug were
found and fixed. Verifying against the live sandbox is what surfaced them — the
original `_revinclude` check passed because it was run with 2 patients, well below the
1000-include cap that breaks it at scale.

The last block was rehearsing this README: cloning into an empty directory, following
it verbatim, and pointing `FHIR_BASE_URL` at an unreachable host to see what a bad day
looks like. That found a real bug (REVIEW.md §12) that 44 passing tests and several
successful migrations had not, because every one of them started from a working server.

**A note on the numbers in this repo.** The sandbox is shared and live — it grew from
6,954 to 7,205 patients over the couple of days this was built. Counts quoted here and
in REVIEW.md are measurements from specific runs, so expect your own run to differ
slightly. The validation summary compares against whatever the server reports at the
time, not against these figures.

## AI Usage

This project was built with assistance from [Devin](https://devin.ai) (Cognition AI). The work was collaborative:

- **Plan.md**: AI explored the FHIR R4 sandbox API to understand the data model, pagination, and `_revinclude` capability, then drafted the migration plan. I reviewed and approved every decision.
- **Backend**: AI wrote the Django models, FHIR client, transform functions, migration command, API endpoints, and tests. I reviewed each commit, tested the migration against the live sandbox, and verified the API responses.
- **Frontend**: AI set up the Vite + Tailwind + shadcn-vue scaffold and wrote the Vue components. I reviewed the component structure and verified the build.
- **FHIR API research**: AI used `curl` to explore the HAPI FHIR sandbox — testing pagination, `_revinclude`, `$everything`, resource counts, and edge cases like deleted resources. These findings informed the migration plan and the client implementation.
- **Review pass**: I then had AI review the working slice as an adversarial code reviewer and implement the fixes, with every finding verified against the live sandbox rather than taken on assertion. [REVIEW.md](REVIEW.md) documents what it found, the measurements behind each claim, and what I deliberately left undone. The most important find — `_revinclude` silently dropping ~90% of observations — came out of that pass.

All technical decisions — the fetch strategy, upsert-based idempotency, batch transactions, exponential backoff with jitter, and the simplified data model — were discussed and agreed upon before implementation.

## What I'd Do Next

Given more time, in priority order:

1. **Incremental sync**: Track `meta.lastUpdated` and only fetch records modified since the last run, turning the one-time migration into a periodic sync. Also needs tombstone handling — nothing currently removes a patient that was deleted at the source.
2. **Observation `component` arrays**: Blood pressure panels carry systolic/diastolic in `component` rather than `valueQuantity`, so they store a NULL value today (3,703 of 37,749 rows — the migration reports the count). A production model would flatten each component into its own row.
3. **Concurrency**: The fetch is sequential. At 50,000 patients a bounded worker pool over patient pages, sharing one rate limiter, would cut wall-clock time substantially — pages are already independent, and the checkpoint would become a low-water mark rather than a single offset.
4. **Drift-proof resumption**: Offset paging means a patient inserted before the cursor can shift the window and cause a resumed run to skip a record. A `_lastUpdated`-windowed sweep would close that gap; today the final count comparison catches it and a re-run converges.
5. **Component-level tests**: Vitest for the Vue components — list rendering, navigation, error and retry states.
6. **Observation pagination in the API**: `GET /api/patients/{id}/` returns every observation inline. Fine at ~100 per patient, not at 10,000.
