# Code review follow-up

Notes from a review pass over the first working slice, and what changed as a result.
Ordered by severity. Nothing here is committed yet.

---

## 1. Silent observation data loss — `_revinclude` is capped at 1000 per page

**The bug.** The client fetched patients and observations in one search:

```
GET /Patient?_count=100&_revinclude=Observation:patient
```

HAPI caps included resources at **1000 per page** and drops the remainder with no
`OperationOutcome` warning. Measured against the live sandbox:

| Request | Patients returned | Observations returned | Distinct patients with observations |
|---|---|---|---|
| `_count=5` | 5 | 494 | 3 |
| `_count=100` | 100 | **exactly 1000** | **20** |

At the default page size of 100, 80 of every 100 patients silently received zero
observations. Server-wide the ratio is ~6 observations per patient (42,000 / 6,954),
so roughly 90% of the clinical data never arrived. `Bundle.link[next]` pages the
*primary* matches only, so the dropped observations were never fetched later either.

The old local `db.sqlite3` shows the fingerprint exactly: 100 patients, exactly 1000
observations, and `0` in the observations column for most rows in the UI.

**The fix.** Patients and observations are now two separate paginated searches
(`api/fhir/client.py`):

- `iter_patient_pages()` — `GET /Patient?_count=N&_sort=_id`. `_sort=_id` makes the
  paging order deterministic across runs.
- `iter_observation_pages(patient_ids)` — `GET /Observation?subject=Patient/a,Patient/b,…`
  in batches of 50 references, each batch paginated to exhaustion via `next`.

Costs a handful of extra requests per page; returns everything. Verified on 20
high-volume patients: **1,934 observations across 10 pages, all 20 subjects covered** —
where the include-based approach would have stopped at 1,000.

There is a regression test (`test_patient_search_does_not_use_revinclude`) so this
does not quietly come back.

**Also fixed:** the validation block fetched `expected_observations` and printed it
but only ever compared the *patient* count, so the loss produced no warning. Both
counts are now checked.

**And the expected count itself was wrong.** Comparing against
`Observation?_summary=count` (42,000) is misleading: 4,070 of those observations have
no `subject` at all, so they can never belong to a patient in this model. The
comparison now uses `Observation?subject:missing=false&_summary=count` (37,930), which
is directly comparable. A validation check that always reports a ~4,000-record gap is
worse than no check — it trains you to ignore it, which is precisely how a real gap
would have slipped through.

Full run after the fix:

```
Patients written:      6954
Observations written:  37749        (was 1000)
  ✓ Patient: 6954 written matches server total
  ~ Observation: wrote 37749 of 37930 reported by the server (181 unaccounted for
    — the remainder reference a subject that is not a migrated patient)
```

365 HTTP requests for 6,954 patients and 37,749 observations. The residual 181 are
observations whose subject is a non-Patient reference (Group, Device) or a patient id
that doesn't resolve on the server — skipped deliberately and counted, not lost.

---

## 2. The missing patient names were stale data, not a live bug

Worth recording because the diagnosis matters more than the symptom.

Every patient in the local DB had `full_name`, `family_name`, `identifier_*` = NULL,
and every observation had `code`, `code_system`, `code_display`, `category` = NULL —
while `code_text`, `gender`, `birthDate` and `valueQuantity.*` were populated.

That split is the signature of a `_safe_get` that could not index into lists: every
NULL field is behind a list index (`name[0]`, `identifier[0]`, `code.coding[0]`,
`category[0]`) and every populated field is not. The original implementation
(commit `bc66fd6`) did exactly that:

```python
for key in keys:
    if not isinstance(current, dict):
        return default          # any list index bails out here
```

It was fixed two commits later in `abf3f8c`, but `db.sqlite3` had already been
populated and was never re-migrated. The current transform maps names correctly —
confirmed by running it over a live 100-patient bundle.

**Guard added.** The command now reports data-quality counters and fails a
validation check when a field is NULL on *every* migrated row:

```
Validation
  ✗ every migrated row is missing patient names — check the transform mapping
```

A whole column of nulls is almost always a broken mapping rather than sparse source
data. This is the check that would have caught the original bug on the first run.

---

## 3. Catching exceptions inside `transaction.atomic()`

The write loop wrapped a whole page in `transaction.atomic()` and caught
`Exception` per record. Django flags the transaction `needs_rollback` when a
`DatabaseError` escapes into an atomic block, so swallowing one bad row makes every
*subsequent* query in that block raise `TransactionManagementError` — one bad record
would take down the remaining ~1,100 records on the page, all counted as generic
failures. SQLite hid this (it does not enforce `max_length`); Postgres, which Plan.md
targets, would not.

Restructured so the two concerns are separated:

- Transform and validate every record first — pure Python, so per-record errors are
  caught safely with no transaction open.
- Write the clean rows with a single `bulk_create(update_conflicts=True)` inside a
  short `atomic()` block.

No exceptions are caught inside an atomic block any more.

---

## 4. Migration reported success on failure

`self.stderr.write(...)` followed by `return` exits **0**. A scheduler would record an
aborted migration as a success. All failure paths now `raise CommandError`, which is
Django's convention and exits 1:

- couldn't read expected counts
- upstream fetch failed mid-run (partial results are reported, and already-written
  pages stay written — re-running is safe)
- zero patients written

---

## 5. Retry logic

Rewrote `_fetch_with_retry`:

| Before | After |
|---|---|
| `raise_for_status()` raised `HTTPError`, caught as `RequestException` → 404s and 400s retried 3× | Only `429/502/503/504` retried; other non-2xx fails immediately |
| `Retry-After` ignored on 429 | Honoured (numeric form), capped at 60s, falls back to backoff for HTTP-date |
| Slept after the final attempt, then raised a message with no status code | No trailing sleep; the failure reason is always in the message |
| Unbounded `2 ** attempt` | Capped at 60s |

Six tests cover this (`FhirClientRetryTest`) — previously the retry path had no test
at all, despite being the failure-handling the brief explicitly asks for.

---

## 6. N+1 queries in the write path

Each observation did `Patient.objects.filter(...).exists()`, and each upsert did a
SELECT-then-UPDATE: ~3 round trips per resource, so ~1M queries at the 50,000-patient
scale in the brief.

- The existence check is now a set-membership test against the ids just written.
- `update_or_create` per row became one `bulk_create(update_conflicts=True)` per page,
  with in-batch de-duplication so `ON CONFLICT` never touches a row twice in one
  statement.

---

## 7. Transform correctness

- **Subject references.** `subject_ref.replace("Patient/", "")` only handled the
  relative form and replaced every occurrence. Absolute references
  (`http://host/baseR4/Patient/123`) passed through untouched and became bogus FKs.
  Now handles relative, absolute and `_history`-versioned references, and returns
  `None` for contained (`#p1`) and non-Patient subjects so the caller skips them.
- **`effective[x]`.** Only `effectiveDateTime` was read. R4 also allows
  `effectivePeriod` and `effectiveInstant`; those rows got a NULL date and sorted to
  the bottom of the patient timeline. Both are now used as fallbacks. `effectiveTiming`
  is deliberately not supported — it is a schedule, not a point in time.
- **Decimals.** Raw floats went into a `DecimalField`, so Django built the Decimal
  from the binary float and inherited its representation error. Now converted via
  `Decimal(str(value))`, with unparseable or out-of-range values stored as NULL and
  logged by id (never by value).
- **`active: null`.** An explicit null would have hit a NOT NULL column. Absent or
  null now means `True` (FHIR treats absence as "no assertion"), `false` stays false.
- **`full_name`.** Built from all `given` parts rather than just the first.

---

## 8. API contract: decimals serialised as strings

DRF's default returned `"value": "126.0000"`, which the UI rendered literally as
**"126.0000 mg/dL"**. Set `COERCE_DECIMAL_TO_STRING = False` so values are JSON
numbers, and the frontend formats them for display.

Also widened `Observation.value` from `max_digits=12, decimal_places=4` to
`19, 6` (migration `0002_widen_observation_value`) so the full range of UCUM lab
values fits without rounding or overflowing, and dropped the redundant `unique=True`
on both primary keys (it created a superfluous second index).

---

## 9. Frontend

- **Dates shifted by a day.** `new Date('1952-01-01')` parses as UTC midnight, and
  `toLocaleDateString` renders in local time, so every date of birth showed a day
  early west of UTC (verified: `Dec 31, 1951` in `America/Los_Angeles`, `Jan 1, 1952`
  in `Europe/London`). Date-only strings are now parsed as local time.
- **Duplicated formatters** in both views moved to `src/lib/format.js`.
- **Fetching and state moved into composables** (`usePatients`, `usePatient`,
  `useMigrationStatus`). Both views had grown request orchestration, pagination
  bookkeeping and race guards inline; the `<script setup>` blocks are now 23 and 31
  lines and are essentially imports. Pagination is exposed as
  `nextPage`/`previousPage`/`hasNextPage` rather than a writable `page` ref, because
  the page number and the rows it describes have to change together.
- **`groupedObservations` was a `ref` mutated imperatively** inside the fetch
  function — derived state that could drift from `patient`. Now a `computed`.
- **Error states.** A failed first load showed the red banner *and* "No patients
  found. Run `migrate_fhir`" at the same time. Error / loading / empty / loaded are
  now mutually exclusive, and both views offer a "Try again" action instead of a
  dead end.
- **Stale record on error.** `PatientDetail` kept the previous patient when a reload
  failed, rendering it beneath the error. It now clears.
- **Race guard.** Concurrent `PatientDetail` loads (route param change mid-flight)
  could apply out of order; the newest request now wins.
- **Keyboard access.** Table rows navigated on `@click` only, with no `href` and no
  tab stop. They now expose `role="link"`, a tab index, Enter/Space handlers, an
  aria-label and a focus ring.
- `encodeURIComponent` on the patient id; named route instead of a hardcoded `'/'`.

---

## 10. Configuration and observability

- **Logging did nothing.** Both modules used `logging.getLogger(__name__)`, but
  Django's default config only wires up the `django` logger, so every `logger.info`
  went nowhere. Added a `LOGGING` block; the `api` logger now writes to console at a
  level set by `MIGRATION_LOG_LEVEL`.
- **Env-driven settings.** `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` and the FHIR base
  URL / page sizes / retry budget / timeout now read from the environment with
  local-dev fallbacks, so nothing needs editing to run and no secret is hardcoded.
- **HTTP session lifecycle.** `FhirClient` is now a context manager and closes its
  `requests.Session`.
- `get_resource_count` returns `None` rather than `0` when the server omits `total`,
  so "unknown" cannot read as "verified zero".

---

## Tests

13 → 44. The new ones cover the parts that were previously untested:

- `FhirClientRetryTest` — transient retry, `Retry-After`, timeouts, fail-fast on 4xx,
  exhaustion, and that the failure reason survives into the exception.
- `FhirClientPaginationTest` — `next`-link traversal, non-matching entry types
  ignored, observation batching (the 51st id is not dropped), and the `_revinclude`
  regression guard.
- `MigrateFhirCommandTest` — end-to-end through `call_command` with a stubbed client:
  mapping, idempotency on re-run, in-page duplicate ids, skipping unresolvable
  subjects, the all-nulls canary, `CommandError` on upstream failure, and that a
  partial run skips the count comparison.
- `MigrationCheckpointTest` — the checkpoint advances per page, survives a failure,
  resumes at the right offset without refetching completed pages, `--restart`
  overrides it, `--max-pages` leaves it resumable, and a finished run is not reused.
- Transform coverage for every reference form, `effective[x]` fallbacks, decimal
  precision, unstorable values, and `active: null`.

Run with `python manage.py test api`.

---

## 11. Resumable checkpointing (second pass)

Plan.md described a `migration_state` table; the slice didn't have one, and the README
claimed a resumability it didn't have. Now built as `MigrationRun`.

**I got the cursor wrong first, then verified it.** My initial note said resumption
should use `_sort=_id&_id=gt<last_id>`. That does not work — `_id` is a *token* search
parameter, and token parameters don't take comparison prefixes:

```
GET /Patient?_count=3&_sort=_id&_id=gt131401721A   → 0 results (matched as a literal id)
```

The mechanism that does work is `_sort=_id` plus `_offset`, confirmed against the
sandbox:

```
GET /Patient?_count=6&_sort=_id             → [0c7072e7…, 0eac7124…, 131401721A, 137202485, …]
GET /Patient?_count=6&_sort=_id&_offset=3   → [137202485, 137202505, 137202507, …]
```

Which means Plan.md's original `last_processed_offset` was right and my "correction"
was the error. Both documents now say so.

**Ordering.** The checkpoint advances only after a page's patients *and* all their
observations commit. A crash in between replays the page — harmless, because the
writes are upserts. Checkpointing first would skip it. At-least-once by construction.

**Demonstrated end to end**, not just unit-tested:

```
$ python manage.py migrate_fhir --page-size 25 --restart
  … 44 pages …
*** SIGKILL (simulated crash) ***

$ python manage.py shell -c "print(MigrationRun.objects.first())"
  status: running   patients_offset: 1100   resumable(): True

$ python manage.py migrate_fhir
Resuming the running run started 2026-08-23 16:17:50 at patient offset 1100
  Page 1 (offset 1125): 25 patients read | cumulative 1125 patients, 5765 observations
  …
  Status: complete   Patient offset: 7013
```

Also in this pass:

- **`--restart`** to abandon a checkpoint deliberately, and a completed run is never
  resumed (a later run starts fresh rather than reusing a stale cursor).
- **Validation now queries the database** instead of in-process counters. The counters
  describe what we believe happened; the table is what actually happened, and a
  resumed run may have replayed a page.
- **stdout is flushed per page.** Python block-buffers stdout when it isn't a tty, so
  the progress lines from a long run piped to a log file only appeared at exit — and
  vanished entirely when I killed the process. Progress you can't see in a log
  pipeline isn't observability. Found by watching the crash demo, not by reading code.
- **Surplus rows are reported distinctly.** The live run printed
  `(-1 unaccounted for)` because the shared sandbox grew from 6,954 to 7,013 patients
  mid-session while our DB still held a row the server no longer lists. Having *more*
  rows than the source is a deletion signal, not a shortfall, and now reads as such.
  That distinction only showed up by running against real, moving data.
- **`/api/migration-status/`** returns the latest run (status, offset, counters,
  error), and the patient list shows a banner when the last run didn't finish, so an
  incomplete migration isn't silently presented as the full dataset.

## 12. Phantom run rows (found by rehearsing the reviewer's setup)

Found by cloning the repo fresh and walking the README as a reviewer would, then
pointing `FHIR_BASE_URL` at an unreachable host to check the failure message.

`_start_run` created the `MigrationRun` row *before* reading the server's expected
counts. When that read failed, the `CommandError` propagated and left a `running` row
at offset 0 that nothing would ever mark failed. Consequences, in order of how much
they'd matter to someone using this:

1. The patient list showed the amber "migration incomplete at offset 0" banner over a
   complete 7,205-patient dataset — the warning that exists to prevent
   misinterpretation became the thing causing it.
2. The status was simply untrue: `running` with no process running.
3. No `error` was recorded, so nothing explained why.

Anyone whose first attempt hit a sandbox blip would have seen this. Fixed by reading
the counts before touching any run row, so a startup failure persists nothing at all.
Two tests cover it; both fail against the previous code.

The general lesson: exercising the *setup path from scratch* found a bug that 44
passing tests and several full migrations did not, because the tests all started from
a working server.

## Deliberately not done

- **`component` arrays** on panel observations (blood pressure systolic/diastolic).
  Still stored with a NULL value; the report counts them so the gap is visible.
- **Request cancellation** (`AbortController`) on the frontend. The pagination
  controls unmount while loading, so there is no reachable race in the list view.
- **Nested observation pagination** in the patient detail response — a patient with
  thousands of observations returns them all in one payload. Pagination was listed as
  out of scope for the exercise.
- **Drift-proof resumption.** Offset paging means a patient inserted *before* the
  cursor shifts the window, so a resumed run could skip a record. Closing that needs a
  `_lastUpdated`-windowed sweep. Today the final count comparison catches the
  discrepancy and a re-run converges, which felt like the right trade for the scope.
- **Tombstones.** Nothing removes a local record deleted at the source; the count
  check surfaces it as a surplus rather than fixing it.
