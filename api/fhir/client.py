"""FHIR R4 API client with pagination, retry, and _revinclude support."""

import logging
import time
import random

import requests

logger = logging.getLogger(__name__)

FHIR_BASE_URL = "https://hapi.fhir.org/baseR4"
DEFAULT_PAGE_SIZE = 100
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30  # seconds
RETRY_STATUS_CODES = {429, 503, 502, 504}


class FhirApiError(Exception):
    """Raised when the FHIR API returns a non-retryable error."""

    pass


class FhirClient:
    """
    Fetches Patient and Observation resources from a FHIR R4 server.

    Uses _revinclude=Observation:patient to fetch patients and their
    observations in a single paginated request sequence, minimizing
    API calls compared to per-patient fetching.
    """

    def __init__(
        self,
        base_url=FHIR_BASE_URL,
        page_size=DEFAULT_PAGE_SIZE,
        max_retries=MAX_RETRIES,
        timeout=REQUEST_TIMEOUT,
        delay_between_pages=0.1,
    ):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.max_retries = max_retries
        self.timeout = timeout
        self.delay_between_pages = delay_between_pages
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/fhir+json",
            "Accept-Charset": "utf-8",
        })

    def _fetch_with_retry(self, url):
        """Fetch a URL with exponential backoff + jitter on retryable errors."""
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code in RETRY_STATUS_CODES:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "FHIR API returned %d, retrying in %.1fs (attempt %d/%d)",
                        response.status_code, wait, attempt + 1, self.max_retries,
                    )
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Request timed out, retrying in %.1fs (attempt %d/%d)",
                        wait, attempt + 1, self.max_retries,
                    )
                    time.sleep(wait)
                    continue
                raise FhirApiError(f"Request timed out after {self.max_retries} retries: {url}")
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Request failed: %s, retrying in %.1fs (attempt %d/%d)",
                        e, wait, attempt + 1, self.max_retries,
                    )
                    time.sleep(wait)
                    continue
                raise FhirApiError(f"Request failed after {self.max_retries} retries: {e}")

        raise FhirApiError(f"Exhausted {self.max_retries} retries for {url}")

    def _find_next_link(self, bundle):
        """Extract the 'next' pagination URL from a FHIR Bundle."""
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                return link["url"]
        return None

    def fetch_patients_with_observations(self):
        """
        Generator that yields batches of (patients, observations) from the FHIR server.

        Each batch is one page of the paginated response. Patients and
        observations are separated by resourceType. The generator follows
        the 'next' link until no more pages remain.

        Yields:
            tuple: (list_of_patient_dicts, list_of_observation_dicts)
        """
        initial_url = (
            f"{self.base_url}/Patient"
            f"?_count={self.page_size}"
            f"&_revinclude=Observation:patient"
        )

        url = initial_url
        page_num = 0

        while url:
            page_num += 1
            logger.info("Fetching FHIR page %d...", page_num)

            bundle = self._fetch_with_retry(url)
            entries = bundle.get("entry", [])

            patients = []
            observations = []

            for entry in entries:
                resource = entry.get("resource", {})
                resource_type = resource.get("resourceType")

                if resource_type == "Patient":
                    patients.append(resource)
                elif resource_type == "Observation":
                    observations.append(resource)

            logger.info(
                "Page %d: %d patients, %d observations",
                page_num, len(patients), len(observations),
            )

            yield patients, observations

            url = self._find_next_link(bundle)
            if url:
                time.sleep(self.delay_between_pages)

    def get_resource_count(self, resource_type):
        """Get the total count of a resource type from the FHIR server."""
        url = f"{self.base_url}/{resource_type}?_summary=count"
        data = self._fetch_with_retry(url)
        return data.get("total", 0)
