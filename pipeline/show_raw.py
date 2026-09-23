"""Print full raw records from the cache, exactly as Airtable returned them.

    python3 -m pipeline.show_raw                          # 5 each from Applications, Candidates, Offers
    python3 -m pipeline.show_raw Offers -n 10
    python3 -m pipeline.show_raw Applications --random --seed 7

Default is the first N records in cache (API) order, so output is reproducible.
Credential-shaped strings are redacted; nothing else is altered.
"""
import argparse
import json
import random

from pipeline.airtable import ROOT
from pipeline.profile import redact

RAW = ROOT / "data" / "raw"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("tables", nargs="*", default=["Applications", "Candidates", "Offers"])
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--random", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    for t in a.tables:
        recs = json.loads((RAW / f"{t}.json").read_text())["records"]
        rows = random.Random(a.seed).sample(recs, min(a.n, len(recs))) if a.random else recs[: a.n]
        print(f"===== {t}: {len(rows)} of {len(recs)} records =====")
        for r in rows:
            print(redact(json.dumps(r, indent=2, ensure_ascii=False)))


if __name__ == "__main__":
    main()
