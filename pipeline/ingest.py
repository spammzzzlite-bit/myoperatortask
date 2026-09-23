"""Download every table in the base once and cache it to data/raw/.

    python3 -m pipeline.ingest            # use the cache if it already exists
    python3 -m pipeline.ingest --refresh  # re-pull from Airtable

Outputs (all git-ignored; this is a customer's production data):
    data/raw/<Table>.json   {"table", "fetched_at", "pages", "record_count", "records": [...]}
    data/raw/_schema.json   meta-API schema, or null if the token lacks schema scope
    data/raw/_manifest.json record counts, page counts and sha256 of each file

Records are stored exactly as the API returned them (id, createdTime, fields).
Note: Airtable omits empty fields from `fields`, so a missing key means empty.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone

from pipeline.airtable import ROOT, Airtable, AirtableError

RAW = ROOT / "data" / "raw"

# Tables named in the exercise brief; used if the meta API is not available.
BRIEF_TABLES = ["Departments", "People", "Job Openings", "Candidates",
                "Applications", "Interviews", "Offers", "Findings"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(refresh=False):
    manifest_path = RAW / "_manifest.json"
    if manifest_path.exists() and not refresh:
        m = json.loads(manifest_path.read_text())
        print(f"Cache present (fetched {m['fetched_at']}); pass --refresh to re-pull.")
        return m

    RAW.mkdir(parents=True, exist_ok=True)
    at = Airtable()
    t0 = time.monotonic()
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print("Fetching schema...")
    schema = at.base_schema()
    (RAW / "_schema.json").write_text(json.dumps(schema, indent=2))
    tables = [t["name"] for t in schema] if schema else BRIEF_TABLES
    extra = sorted(set(tables) - set(BRIEF_TABLES))
    missing = sorted(set(BRIEF_TABLES) - set(tables))
    if extra:
        print(f"  tables not named in brief: {extra}")
    if missing:
        print(f"  tables named in brief but not in schema: {missing}")

    manifest = {"base_id": at.base_id, "fetched_at": fetched_at, "tables": {}}
    for table in tables:
        records, pages = at.list_records(table)
        ids = [r["id"] for r in records]
        if len(ids) != len(set(ids)):
            raise AirtableError(f"{table}: duplicate record IDs across pages — pagination bug")
        out = RAW / f"{table}.json"
        out.write_text(json.dumps({"table": table, "fetched_at": fetched_at, "pages": pages,
                                   "record_count": len(records), "records": records}, indent=1))
        manifest["tables"][table] = {"file": out.name, "record_count": len(records),
                                     "pages": pages, "sha256": sha256(out)}
        print(f"  {table:<14} {len(records):>6} records  {pages:>3} pages")

    manifest["requests"] = at.request_count
    manifest["elapsed_s"] = round(time.monotonic() - t0, 1)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Done: {at.request_count} requests in {manifest['elapsed_s']}s -> {RAW}/")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    try:
        main(p.parse_args().refresh)
    except AirtableError as e:
        sys.exit(f"ERROR: {e}")
