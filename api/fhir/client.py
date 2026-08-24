"""FHIR R4 API client with pagination and bounded retry/backoff."""

import logging
import random
import time
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Retried because they are transient by definition. Other 4xx/5xx responses mean
# the request itself is wrong (or the server is broken in a way retrying won't
# fix), so they fail immediately rather than burning the retry budget.
RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
MAX_BACKOFF_SECONDS = 60

# How many patient references to OR together in one Observation search. Keeps the
# query string well clear of typical 8KB server/proxy request line limits.
OBSERVATION_SUBJECT_BATCH = 50

# URLs appear in logs; observation searches embed patient ids, which our logging
# policy permits, but a 50-reference query string is unreadable noise.
LOG_URL_MAX_LENGTH = 160


class FhirApiError(Exception):
    """Raised when the FHIR API cannot be read successfully."""

    pass


def _chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _log_url(url):
    return url if len(url) <= LOG_URL_MAX_LENGTH else f"{url[:LOG_URL_MAX_LENGTH]}..."


class FhirClient:
    """
    Reads Patient and Observation resources from a FHIR R4 server.

    Patients and observations are fetched with two separate paginated searches
    rather than a single `_revinclude=Observation:patient` query.

    `_revinclude` looks attractive — one request instead of two — but HAPI caps
    included resources at 1000 per page and drops the remainder *without any
    OperationOutcome warning*. At `_count=100` that means only the first ~20
    patients on a page get observations and the rest silently get none; the
    `next` link pages the primary matches only, so the dropped observations are
    never fetched. Paginating `Observation?subject=` per batch of patients costs
    a handful of extra requests and returns every observation.

    Usable as a context manager so the underlying HTTP session is closed.
    """

    def __init__(
        self,
        base_url=None,
        page_size=None,
        observation_page_size=None,
        max_retries=None,
        timeout=None,
        delay_between_pages=0.1,
    ):
        self.base_url = (base_url or settings.FHIR_BASE_URL).rstrip("/")
        self.page_size = page_size or settings.FHIR_PAGE_SIZE
        self.observation_page_size = observation_page_size or settings.FHIR_OBSERVATION_PAGE_SIZE
        self.max_retries = max_retries or settings.FHIR_MAX_RETRIES
        self.timeout = timeout or settings.FHIR_REQUEST_TIMEOUT
        self.delay_between_pages = delay_between_pages
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/fhir+json",
            "Accept-Charset": "utf-8",
        })

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        self.session.close()

    def _backoff_seconds(self, attempt, retry_after=None):
        """Seconds to wait before the next attempt, honouring Retry-After when sent."""
        if retry_after:
            try:
                return min(max(float(retry_after), 0), MAX_BACKOFF_SECONDS)
            except ValueError:
                # Retry-After may be an HTTP-date; fall back to plain backoff.
                pass
        return min((2 ** attempt) + random.uniform(0, 1), MAX_BACKOFF_SECONDS)

    def _fetch_with_retry(self, url):
        """
        Fetch a URL, retrying transient failures with exponential backoff + jitter.

        Raises FhirApiError on a non-retryable response or once retries are
        exhausted; the message always carries the reason so callers can tell a
        404 from a connection reset.
        """
        last_error = None

        for attempt in range(self.max_retries):
            retry_after = None
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.exceptions.Timeout:
                last_error = f"request timed out after {self.timeout}s"
            except requests.exceptions.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
            else:
                if response.status_code in RETRY_STATUS_CODES:
                    last_error = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After")
                elif not response.ok:
                    raise FhirApiError(
                        f"HTTP {response.status_code} (not retryable) for {_log_url(url)}"
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as e:
                        # Truncated or malformed body — worth one more attempt.
                        last_error = f"invalid JSON response: {e}"

            if attempt == self.max_retries - 1:
                break

            wait = self._backoff_seconds(attempt, retry_after)
            logger.warning(
                "FHIR request failed (%s), retrying in %.1fs (attempt %d/%d): %s",
                last_error, wait, attempt + 1, self.max_retries, _log_url(url),
            )
            time.sleep(wait)

        raise FhirApiError(
            f"Giving up after {self.max_retries} attempts ({last_error}) for {_log_url(url)}"
        )

    def _next_link(self, bundle):
        """Extract the 'next' pagination URL from a FHIR Bundle."""
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                return link.get("url")
        return None

    def _iter_bundle_pages(self, url, description):
        """Yield each Bundle in a search result, following Bundle.link[next]."""
        page_num = 0
        while url:
            page_num += 1
            logger.info("Fetching %s page %d", description, page_num)
            bundle = self._fetch_with_retry(url)
            yield bundle

            url = self._next_link(bundle)
            if url and self.delay_between_pages:
                time.sleep(self.delay_between_pages)

    @staticmethod
    def _resources(bundle, resource_type):
        return [
            entry["resource"]
            for entry in bundle.get("entry", [])
            if isinstance(entry.get("resource"), dict)
            and entry["resource"].get("resourceType") == resource_type
        ]

    def iter_patient_pages(self, start_offset=0):
        """
        Yield each page of Patient resources from the server.

        Sorted by _id so the paging order is deterministic — without it HAPI's
        default ordering can shift between runs, which would make `start_offset`
        meaningless.

        `start_offset` resumes a previous run via `_offset`. Note that `_id` is a
        token search parameter, so the keyset form (`_id=gt<last>`) is not
        available — HAPI matches it as a literal id and returns nothing.
        """
        params = {"_count": self.page_size, "_sort": "_id"}
        if start_offset:
            params["_offset"] = start_offset
        query = urlencode(params)
        for bundle in self._iter_bundle_pages(f"{self.base_url}/Patient?{query}", "patient"):
            yield self._resources(bundle, "Patient")

    def iter_observation_pages(self, patient_ids):
        """
        Yield pages of Observations belonging to the given patient ids.

        Patient ids are OR'd together in batches so one search covers many
        patients, and each batch is paginated to exhaustion.
        """
        patient_ids = [pid for pid in patient_ids if pid]
        for batch in _chunked(patient_ids, OBSERVATION_SUBJECT_BATCH):
            query = urlencode({
                "subject": ",".join(f"Patient/{pid}" for pid in batch),
                "_count": self.observation_page_size,
            })
            pages = self._iter_bundle_pages(
                f"{self.base_url}/Observation?{query}",
                f"observation (batch of {len(batch)} patients)",
            )
            for bundle in pages:
                yield self._resources(bundle, "Observation")

    def get_resource_count(self, resource_type, search_params=None):
        """
        Total number of a resource type on the server, or None if it won't say.

        Distinguishing "unknown" from zero matters: the count is only used to
        validate the migration, and a missing total must not read as success.
        """
        query = urlencode({"_summary": "count", **(search_params or {})})
        bundle = self._fetch_with_retry(f"{self.base_url}/{resource_type}?{query}")
        total = bundle.get("total")
        return total if isinstance(total, int) else None
