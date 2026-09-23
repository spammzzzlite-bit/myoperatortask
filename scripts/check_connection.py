"""Raw Airtable connectivity check.

Reads AIRTABLE_TOKEN / AIRTABLE_BASE_ID from .env (or the environment),
fetches a handful of records from one table, and confirms the token is
read-only. Prints status codes, record counts and record IDs only — never
the token and never record contents.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / ".env"):
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def request(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:
            payload = {}
        return e.code, payload


def main(table="Applications", n=3):
    load_env()
    token = os.environ.get("AIRTABLE_TOKEN")
    base = os.environ.get("AIRTABLE_BASE_ID")
    if not token or not base:
        sys.exit("AIRTABLE_TOKEN / AIRTABLE_BASE_ID not set")
    url = f"https://api.airtable.com/v0/{base}/{urllib.request.quote(table)}"

    # 1. Read: pull n records.
    try:
        status, body = request("GET", f"{url}?maxRecords={n}", token)
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach api.airtable.com (network, not auth): {e.reason}")
    recs = body.get("records", [])
    print(f"READ  GET {table}?maxRecords={n} -> HTTP {status}, {len(recs)} records")
    for r in recs:
        print(f"      record id: {r.get('id')}")

    # 2. Write probe: POST an empty record list. On a writable token this is a
    #    422 validation error and creates nothing; on a read-only token it is 403.
    status_w, body_w = request("POST", url, token, {"records": []})
    err = body_w.get("error")
    err_type = err.get("type") if isinstance(err, dict) else err
    print(f"WRITE POST {table} (empty, no-op) -> HTTP {status_w} ({err_type})")
    print("Access live:", status == 200 and len(recs) > 0)
    print("Read-only confirmed:", status_w == 403)


if __name__ == "__main__":
    main(*sys.argv[1:2])
