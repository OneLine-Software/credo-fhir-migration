# FHIR R4 Patient Migration

A migration tool and viewer for patient records from a FHIR R4 API (HAPI FHIR sandbox) into an internal service with a simplified data model.

## What this does

1. **Migrates** patient records and observations from the [HAPI FHIR R4 sandbox](https://hapi.fhir.org/baseR4) into a local SQLite database
2. **Exposes** a REST API for browsing migrated patients and their observations
3. **Provides** a Vue frontend for viewing patients and their clinical observations

## Architecture

```
FHIR R4 API → Fetch (retry/backoff) → Transform (flatten JSON) → Persist (upsert) → REST API → Vue Frontend
```

| Layer | Technology |
|-------|-----------|
| Backend | Django 6.1 + Django REST Framework |
| Database | SQLite (no server needed) |
| Frontend | Vue 3 + Vite + Tailwind CSS v4 + shadcn-vue |
| FHIR Client | Python `requests` with pagination and exponential backoff |

## Setup

### Prerequisites

- Python 3.12+
- Node.js 20+

### Backend

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Run database migrations
python manage.py migrate

# Migrate patient data from FHIR sandbox
python manage.py migrate_fhir

# For testing (migrate only 2 pages):
python manage.py migrate_fhir --max-pages 2

# Start the API server
python manage.py runserver
```

The API is now available at `http://localhost:8000/api/`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend is now available at `http://localhost:5173`.

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
| `GET /api/migration-status/` | Patient and observation counts |

## Running Tests

```bash
source .venv/bin/activate
python manage.py test api -v2
```

Tests cover:
- FHIR-to-internal transform functions (field mapping, missing fields, date parsing)
- API endpoints (list, detail, 404, filtering)
- Upsert idempotency (re-running migration doesn't create duplicates)

## Migration Options

```bash
python manage.py migrate_fhir [options]

  --page-size N      Patients per FHIR API page (default: 100)
  --max-pages N      Stop after N pages (for testing)
  --delay SECONDS    Delay between API requests (default: 0.1)
```

The migration is **idempotent** — re-running it updates existing records rather than creating duplicates. Each batch is a single database transaction. If the migration crashes, re-run it and it resumes the upsert from where it left off.

## Project Structure

```
.
├── Plan.md                          # Migration plan (Part 1)
├── manage.py                        # Django management script
├── requirements.txt                 # Python dependencies
├── fhir_migration/                  # Django project settings
│   ├── settings.py
│   └── urls.py
├── api/                             # Django app
│   ├── models.py                    # Patient + Observation models
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
    │   ├── api/index.js             # API client
    │   └── router/index.js          # Vue Router
    └── vite.config.js
```

## AI Usage

This project was built with assistance from [Devin](https://devin.ai) (Cognition AI). The work was collaborative:

- **Plan.md**: AI explored the FHIR R4 sandbox API to understand the data model, pagination, and `_revinclude` capability, then drafted the migration plan. I reviewed and approved every decision.
- **Backend**: AI wrote the Django models, FHIR client, transform functions, migration command, API endpoints, and tests. I reviewed each commit, tested the migration against the live sandbox, and verified the API responses.
- **Frontend**: AI set up the Vite + Tailwind + shadcn-vue scaffold and wrote the Vue components. I reviewed the component structure and verified the build.
- **FHIR API research**: AI used `curl` to explore the HAPI FHIR sandbox — testing pagination, `_revinclude`, `$everything`, resource counts, and edge cases like deleted resources. These findings informed the migration plan and the client implementation.

All technical decisions — the `_revinclude` fetch strategy, upsert-based idempotency, batch transactions, exponential backoff with jitter, and the simplified data model — were discussed and agreed upon before implementation.

## What I'd Do Next

Given more time, these are the areas I'd prioritize:

1. **Incremental sync**: Track `meta.lastUpdated` from FHIR resources and only fetch records modified since the last migration run. This turns the one-time migration into a periodic sync.
2. **Migration state persistence**: A `migration_state` table to track progress across runs — last processed offset, cumulative counts, status. Enables crash recovery without re-processing completed batches.
3. **Referential integrity validation**: Post-migration check for orphaned observations (observations whose `patient_id` doesn't exist in the patients table).
4. **Component-level tests**: Vitest tests for the Vue components — patient list rendering, navigation, error states.
5. **Environment-based config**: Move FHIR base URL, page size, and retry settings into environment variables or a settings file.
6. **Observation component handling**: Some FHIR observations use `component` arrays (e.g., blood pressure panels with systolic + diastolic) instead of a single `valueQuantity`. A production system would flatten these into individual observation rows.
