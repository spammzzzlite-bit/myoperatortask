# TalentFlow × Acme — Q3 roadmap exercise

Context and brief: [`PROJECT_NOTES.md`](PROJECT_NOTES.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then put the Airtable token in AIRTABLE_TOKEN
```

The token is read from `.env` / the environment only. It is never printed, logged or written to disk elsewhere.

## Pipeline

| Step | Command | Output |
|---|---|---|
| Connectivity check (3 records + no-op write probe) | `python3 scripts/check_connection.py` | stdout |
| Pull every table once (paginated, ≤4 req/s, waits out 429s) | `python3 -m pipeline.ingest` (`--refresh` to re-pull) | `data/raw/*.json`, `_schema.json`, `_manifest.json` |
| Schema + data profile (offline) | `python3 -m pipeline.profile` | `data/profile/profile.md`, `profile.json` |
| Show full raw rows (offline) | `python3 -m pipeline.show_raw [Table ...] [-n 5] [--random --seed N]` | stdout |
| Tests (local mock API, synthetic data) | `python3 -m unittest tests.test_pipeline -v` | — |

Everything after ingest reads only from `data/raw/`, so analysis never goes back to the API.
`_manifest.json` records the fetch time, record counts and sha256 of each cached table, so a re-run can be compared with ours.
`data/` is git-ignored because it holds Acme production data, including candidate PII.
