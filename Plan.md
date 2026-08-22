# Migration Plan: FHIR R4 Patient Records → Internal Service

## Overview

Migrate ~50,000 patient records and their associated observations from a legacy
clinical system (FHIR R4 API) into a new internal service. The migration must be
reliable, observable, safe with PHI, and reversible.

---

## 1. Overall Approach

**Strategy: batch-based, idempotent migration with resumability.**

### Fetch strategy

Paginate through all Patient resources using `_count=100`. For each page, use
`_revinclude=Observation:patient` to fetch patients and their observations in a
single paginated request sequence — instead of issuing a separate Observation
query per patient.

With 50K patients averaging ~6 observations each, per-patient fetching would mean
50K+ API calls. The `_revinclude` approach batches this: 100 patients + ~600
observations per page, reducing API calls by an order of magnitude.

### Idempotency

Each patient and observation is upserted by FHIR resource ID (used as the primary
key in our internal model). Re-running the migration for the same patient updates
the existing record rather than creating a duplicate. This makes the migration
safe to restart and re-run.

### Resumability

A `migration_state` table tracks progress:

| field | description |
|-------|-------------|
| `last_processed_offset` | last page offset successfully written |
| `patients_seen` | cumulative count fetched from FHIR |
| `patients_written` | cumulative count persisted |
| `observations_seen` | cumulative count fetched |
| `observations_written` | cumulative count persisted |
| `status` | `running` / `paused` / `complete` / `failed` |
| `last_batch_at` | timestamp of last successful batch |

If the migration crashes, it resumes from the last checkpoint rather than
restarting from zero.

### API limits and backoff

- No rate-limit headers are exposed by the sandbox, but a production FHIR server
  will throttle. Assume limits exist.
- Exponential backoff with jitter on `429 Too Many Requests` and `503 Service
  Unavailable` responses.
- Conservative default page size (100) to avoid oversized responses.
- Configurable delay between page requests (default 100ms).
- Per-request timeout of 30s with retry (max 3 attempts).

### Observability

- **Structured logs**: each batch logs patient count, observation count, elapsed
  time, and cumulative progress percentage.
- **Error log**: failed resources (deleted, invalid, parse errors) are logged
  with the FHIR ID, error type, and response snippet for later retry/analysis.
- **Summary report**: at completion, log total migrated, total skipped, total
  failed, and total duration.

```
PSEUDOCODE — fetch and upsert loop:

state = load_migration_state()
while state.status != "complete":
    patients, observations, next_url = fetch_batch(
        offset=state.last_processed_offset,
        count=100,
        revinclude="Observation:patient"
    )
    with transaction:
        for patient in patients:
            upsert_patient(transform_patient(patient))
        for obs in observations:
            upsert_observation(transform_observation(obs))
        state.last_processed_offset += len(patients)
        state.patients_written += len(patients)
        state.observations_written += len(observations)
        save(state)
    log_batch_progress(state)

    if next_url is None:
        state.status = "complete"
        save(state)
```

---

## 2. Data Mapping

FHIR resources are deeply nested JSON. We flatten them into a simpler internal
schema, keeping only the fields the internal service needs.

### Patient

| FHIR path | Internal field | Type | Notes |
|-----------|---------------|------|-------|
| `Patient.id` | `fhir_id` | string, PK | Unique; used for idempotent upsert |
| `Patient.identifier[0].system` | `identifier_system` | string, nullable | e.g. `https://sindhu-ecrf.local/synthetic-patient-id` |
| `Patient.identifier[0].value` | `identifier_value` | string, nullable | e.g. `SYN-000004` |
| `Patient.active` | `active` | boolean | |
| `Patient.name[0].family` | `family_name` | string, nullable | |
| `Patient.name[0].given[0]` | `given_name` | string, nullable | |
| `Patient.name[0].text` | `full_name` | string, nullable | Falls back to constructed `given family` |
| `Patient.gender` | `gender` | string, nullable | |
| `Patient.birthDate` | `birth_date` | date, nullable | |
| — | `migrated_at` | timestamp | Set on each upsert |

### Observation

| FHIR path | Internal field | Type | Notes |
|-----------|---------------|------|-------|
| `Observation.id` | `fhir_id` | string, PK | Unique; used for idempotent upsert |
| `Observation.subject.reference` | `patient_id` | string, FK → `Patient.fhir_id` | Strip `Patient/` prefix from `"Patient/{id}"` |
| `Observation.status` | `status` | string | e.g. `final`, `preliminary`, `cancelled` |
| `Observation.category[0].coding[0].code` | `category` | string, nullable | e.g. `laboratory`, `vital-signs` |
| `Observation.code.coding[0].system` | `code_system` | string, nullable | Typically `http://loinc.org` |
| `Observation.code.coding[0].code` | `code` | string, nullable | LOINC code, e.g. `8310-5` |
| `Observation.code.coding[0].display` | `code_display` | string, nullable | e.g. `Body temperature` |
| `Observation.code.text` | `code_text` | string, nullable | Human-readable label |
| `Observation.effectiveDateTime` | `effective_date` | datetime, nullable | When the observation was taken |
| `Observation.valueQuantity.value` | `value` | decimal, nullable | The measured value |
| `Observation.valueQuantity.unit` | `value_unit` | string, nullable | e.g. `mm[Hg]`, `U/L`, `C` |
| `Observation.valueQuantity.system` | `value_system` | string, nullable | Typically `http://unitsofmeasure.org` |
| `Observation.valueQuantity.code` | `value_code` | string, nullable | UCUM code |
| — | `migrated_at` | timestamp | Set on each upsert |

### Mapping decisions

- **`name[0]` and `identifier[0]`**: FHIR stores these as arrays because a patient
  can have multiple names (maiden, legal, preferred) and multiple identifiers (MRN,
  SSN, insurance). We take the first — the primary. A production system with
  historical name tracking would use a separate `patient_names` table.
- **Subject reference parsing**: `Observation.subject.reference` is the string
  `"Patient/sindhu-syn-000004"`. We split on `/` and take the second part to get
  the foreign key.
- **Nullable values**: Not all observations have `valueQuantity`. Panel
  observations (e.g. a blood pressure panel) may use `component` arrays instead.
  We store `value` as nullable and note this as a known limitation. A production
  system would handle `valueString`, `valueBoolean`, `valueCodeableConcept`, and
  `component` arrays.
- **What we drop**: `meta` (versioning/audit), `encounter` reference, `tag` arrays,
  and `text.div` (FHIR narrative). These are FHIR-internal metadata not needed in
  a simplified clinical model.

---

## 3. Validation

### Pre-migration

Query the FHIR server for total Patient and Observation counts
(`?_summary=count`) and store as expected totals in `migration_state`.

### Post-migration (automated)

1. **Count comparison**: `SELECT COUNT(*) FROM patients` vs. expected total from
   FHIR. Repeat for observations. Flag any mismatch.

2. **Referential integrity**: Check for orphaned observations — observations whose
   `patient_id` doesn't exist in the `patients` table. This catches cases where a
   patient was deleted on the FHIR server mid-migration.

   ```sql
   SELECT COUNT(*) FROM observations o
   LEFT JOIN patients p ON o.patient_id = p.fhir_id
   WHERE p.fhir_id IS NULL;
   -- Expected: 0
   ```

3. **Spot-check sampling**: Randomly select 10 patients from the internal DB,
   fetch the same patient from FHIR, and compare field-by-field. Log any
   discrepancies.

4. **No-data-loss check**: Cumulative `patients_seen` and `observations_seen` from
   the FHIR responses should equal `patients_written` and `observations_written`
   plus any logged failures. If FHIR returned 100 patients in a batch and we wrote
   98, the other 2 should appear in the error log with reasons.

5. **Failed-resource report**: Review all entries in the error log before
   declaring the migration complete. Decide whether failures are acceptable
   (deleted resources) or require investigation (parse errors, schema mismatches).

---

## 4. Safety (PHI Handling)

This exercise uses synthetic data from a public sandbox — no real PHI is involved.
The following applies to a **production version** of this migration:

1. **Encryption in transit**: All API calls over HTTPS/TLS 1.2+. No plaintext.
2. **Encryption at rest**: Database encrypted (Postgres TDE or disk-level
   encryption). Backup snapshots encrypted with a managed KMS key.
3. **No PHI in logs**: Structured logging references patients by FHIR ID or
   internal ID only — never logs name, birth date, or other identifying fields.
   Error logs for failed resources store the FHIR ID and error type, not the
   resource content.
4. **Access controls**: Migration service account has `INSERT`/`UPDATE` on
   patient and observation tables only — no `DROP`, `ALTER`, or access to other
   tables. Principle of least privilege.
5. **Data minimization**: Only map fields the internal service needs. Don't store
   raw FHIR JSON unless required for audit — if stored, encrypt the column.
6. **Audit trail**: Every upsert logged with who (service account), what (FHIR ID),
   when (timestamp), and action (`insert` or `update`). Supports post-migration
   auditing and compliance reviews.
7. **Network security**: Migration runs from within the VPC. FHIR API calls go
   through a restricted egress proxy. No PHI traverses the public internet between
   the migration service and the database.
8. **HIPAA context**: As a Business Associate handling PHI on behalf of a Covered
   Entity, the migration operates under a BAA. All the above controls support
   HIPAA Security Rule requirements (administrative, physical, and technical
   safeguards). The internal service itself must be HIPAA-compliant — this
   migration inherits that compliance posture.

---

## 5. Rollback

**Design principle: the migration is idempotent and reversible at every step.**

1. **Upsert pattern**: Every write is an `INSERT ... ON CONFLICT (fhir_id) DO
   UPDATE`. Re-running the migration converges to the correct state — no
   duplicates, no data loss. "Rolling back" a batch is as simple as re-running it.

2. **Batch-level transactions**: Each batch (100 patients + their observations) is
   a single database transaction. All records in a batch succeed or fail together.
   No partial writes.

3. **Migration state tracking**: The `migration_state` table records the last
   successfully processed offset. On crash, the migration resumes from that
   checkpoint. Failed batches are marked `failed` and retried on the next run;
   completed batches are skipped.

4. **Full rollback** (worst case — schema is wrong or data is corrupted):
   - `TRUNCATE patients, observations, migration_state RESTART IDENTITY`
   - Re-run the migration from scratch — safe because the source is read-only.

5. **Partial rollback** (specific batch introduced bad data):
   - Identify the batch by time window (`migrated_at` falls within the batch's
     range) or patient ID range.
   - Delete those records.
   - Re-run that specific batch.

6. **Source data is never modified**: The migration is read-only on the FHIR
   server. We never `POST`, `PUT`, or `DELETE` against the source system. Rollback
   only affects the destination database — there is no risk of corrupting the
   source.

---

## Data Flow

```
FHIR R4 API (hapi.fhir.org/baseR4)
    │
    │  GET /Patient?_count=100&_revinclude=Observation:patient
    │  (paginated via Bundle.link[rel=next])
    ▼
┌──────────────┐
│  Fetch Layer  │  ← retry w/ exponential backoff + jitter
│  (HTTP client) │  ← 30s timeout, max 3 retries
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Transform Layer│ ← flatten FHIR JSON → internal model
│               │ ← parse subject.reference → patient_id FK
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Persist Layer │ ← upsert (INSERT ON CONFLICT DO UPDATE)
│  (Django ORM)  │ ← batch transaction per page
│  SQLite / PG   │ ← migration_state checkpoint per batch
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   REST API     │ ← GET /api/patients (list)
│   (Django)     │ ← GET /api/patients/{id} (detail + observations)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Vue Frontend  │ ← patient list view
│               │ ← patient detail w/ observations
└──────────────┘
```
