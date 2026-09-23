"""Thin, rate-limited Airtable REST client.

- Reads AIRTABLE_TOKEN / AIRTABLE_BASE_ID from .env or the environment.
- Never logs the token.
- Throttles to stay under Airtable's 5 req/s per-base limit.
- On 429, waits out the 30 s lockout and retries.
- Follows `offset` pagination (max 100 records/page) to exhaustion.
"""
import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent

API_URL = os.environ.get("AIRTABLE_API_URL", "https://api.airtable.com")  # overridable for tests
MIN_INTERVAL_S = 0.25   # 4 req/s: headroom under the 5 req/s limit
LOCKOUT_S = 30          # Airtable's 429 lockout
MAX_RETRIES = 5
PAGE_SIZE = 100


def load_env(path=ROOT / ".env"):
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class AirtableError(Exception):
    pass


class Airtable:
    def __init__(self, token=None, base_id=None, log=print):
        load_env()
        self.token = token or os.environ.get("AIRTABLE_TOKEN")
        self.base_id = base_id or os.environ.get("AIRTABLE_BASE_ID")
        if not self.token or not self.base_id:
            raise AirtableError("AIRTABLE_TOKEN / AIRTABLE_BASE_ID not set (see .env.example)")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.log = log
        self._last = 0.0
        self.request_count = 0

    def __repr__(self):  # keep the token out of any accidental repr/log
        return f"Airtable(base_id={self.base_id!r})"

    def _throttle(self):
        wait = self._last + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, path, params=None):
        url = f"{API_URL}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            self.request_count += 1
            try:
                r = self.session.get(url, params=params, timeout=60)
            except requests.ConnectionError as e:
                raise AirtableError(f"Cannot reach {API_URL} (network, not auth): {e}") from None
            if r.status_code == 429:
                self.log(f"  429 rate-limited; sleeping {LOCKOUT_S}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(LOCKOUT_S)
                continue
            if r.status_code >= 500:
                backoff = 2 ** attempt
                self.log(f"  HTTP {r.status_code}; retrying in {backoff}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(backoff)
                continue
            return r
        raise AirtableError(f"Gave up on {path} after {MAX_RETRIES} attempts")

    def base_schema(self):
        """Table/field metadata. Needs the schema.bases:read scope; returns None if not granted."""
        r = self.get(f"/v0/meta/bases/{self.base_id}/tables")
        if r.status_code == 200:
            return r.json()["tables"]
        self.log(f"  meta API unavailable (HTTP {r.status_code}); schema will be inferred from records")
        return None

    def list_records(self, table):
        """All records in a table, following pagination. Returns (records, pages)."""
        path = f"/v0/{self.base_id}/{quote(table, safe='')}"
        for restart in range(3):
            records, pages, offset = [], 0, None
            while True:
                params = {"pageSize": PAGE_SIZE}
                if offset:
                    params["offset"] = offset
                r = self.get(path, params)
                if r.status_code == 422 and "ITERATOR" in r.text.upper():
                    # Offset expired mid-scan: restart this table from page 1.
                    self.log(f"  pagination offset expired on {table}; restarting table")
                    break
                if r.status_code != 200:
                    raise AirtableError(f"{table}: HTTP {r.status_code} {r.text[:200]}")
                body = r.json()
                records.extend(body.get("records", []))
                pages += 1
                offset = body.get("offset")
                if not offset:
                    return records, pages
        raise AirtableError(f"{table}: pagination kept expiring")
