# TalentFlow × Acme — Q3 roadmap exercise

Context and brief: [`PROJECT_NOTES.md`](PROJECT_NOTES.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then put the Airtable token in AIRTABLE_TOKEN
```

Or, in a Claude Code cloud environment, add the key under **API credentials** (host `api.airtable.com`): the proxy injects it and the code sends no header of its own. Either way, `AIRTABLE_BASE_ID` defaults to the exercise base `appYePRAI75PMbQNQ`.

The token is read from `.env` / the environment only. It is never printed, logged or written to disk elsewhere.

## Pipeline

| Step | Command | Output |
|---|---|---|
| Connectivity check (3 records + no-op write probe) | `python3 scripts/check_connection.py` | stdout |
| Pull every table once (paginated, ≤4 req/s, waits out 429s) | `python3 -m pipeline.ingest` (`--refresh` to re-pull) | `data/raw/*.json`, `_schema.json`, `_manifest.json` |
| Schema + data profile (offline) | `python3 -m pipeline.profile` | `data/profile/profile.md`, `profile.json` |
| Show full raw rows (offline) | `python3 -m pipeline.show_raw [Table ...] [-n 5] [--random --seed N]` | stdout |
| D4 data-quality audit (offline, record IDs only) | `python3 -m pipeline.audit` | `outputs/data_quality.md`, `outputs/data_quality.csv` |
| Tests (local mock API, synthetic data) | `python3 -m unittest tests.test_pipeline tests.test_audit -v` | — |

Everything after ingest reads only from `data/raw/`, so analysis never goes back to the API.
`_manifest.json` records the fetch time, record counts and sha256 of each cached table, so a re-run can be compared with ours.
`data/` is git-ignored because it holds Acme production data, including candidate PII.
`outputs/` is committed: it holds counts and Airtable record IDs only, and the audit refuses to write if a name, email or phone would appear.
