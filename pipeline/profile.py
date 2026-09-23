"""Schema + data profile of every cached table. Reads data/raw/ only; makes no API calls.

    python3 -m pipeline.profile

Writes data/profile/profile.md (readable) and data/profile/profile.json (machine).
Per table: record count, fields, types, example values, likely relationships,
missing-value rates, and value counts for status/category-like fields.

This is descriptive only. It flags shapes (mixed types, formula errors,
unresolved links) but draws no conclusions.
"""
import json
import re
import statistics
from collections import Counter

from pipeline.airtable import ROOT

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "profile"

REC_ID = re.compile(r"^rec[A-Za-z0-9]{14}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?$")
SECRET = re.compile(r"(pat[A-Za-z0-9]{10,}\.[A-Za-z0-9]{20,}|key[A-Za-z0-9]{14}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+\S+)")

MAX_CATEGORIES = 25     # a field with at most this many distinct values is listed in full
EXAMPLES = 3
KEY_UNIQUENESS = 0.95   # distinct/non-empty ratio for a field to count as a candidate key
FK_OVERLAP = 0.5        # share of a field's values found in another table's key to flag a link


def redact(s):
    return SECRET.sub("<REDACTED>", s)


def short(v, n=60):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = redact(s.replace("\n", " "))
    return s if len(s) <= n else s[: n - 1] + "…"


def is_empty(v):
    return v is None or v == "" or v == [] or v == {}


def json_kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        if REC_ID.match(v):
            return "rec_id"
        if ISO_DATE.match(v):
            return "date"
        return "string"
    if isinstance(v, list):
        inner = {json_kind(x) for x in v}
        return "list[" + "|".join(sorted(inner)) + "]" if inner else "list[]"
    if isinstance(v, dict):
        if "error" in v:
            return "formula_error"
        if "specialValue" in v:
            return "special_value"
        return "object"
    return type(v).__name__


def hashable(v):
    return v if isinstance(v, (str, int, float, bool)) else json.dumps(v, sort_keys=True)


def load():
    manifest = json.loads((RAW / "_manifest.json").read_text())
    schema = json.loads((RAW / "_schema.json").read_text())
    data = {t: json.loads((RAW / info["file"]).read_text())["records"]
            for t, info in manifest["tables"].items()}
    return manifest, schema, data


def profile_field(name, meta, values, n, id_index):
    present = [v for v in values if not is_empty(v)]
    kinds = Counter(json_kind(v) for v in present)
    atoms = [x for v in present for x in (v if isinstance(v, list) else [v])]
    distinct = Counter(hashable(a) for a in atoms)
    f = {
        "field": name,
        "type": meta["type"] if meta else "inferred:" + (kinds.most_common(1)[0][0] if kinds else "empty"),
        "json_kinds": dict(kinds),
        "non_empty": len(present),
        "missing_rate": round(1 - len(present) / n, 4) if n else None,
        "distinct": len(distinct),
        "examples": [short(v) for v in list(dict.fromkeys(hashable(v) for v in present))[:EXAMPLES]],
    }
    if meta and meta.get("options"):
        opts = meta["options"]
        if "choices" in opts:
            f["declared_choices"] = [c["name"] for c in opts["choices"]]
        if "linkedTableId" in opts:
            f["declared_link_table_id"] = opts["linkedTableId"]
    flags = []
    if len(kinds) > 1:
        flags.append(f"mixed value kinds {dict(kinds)}")
    if kinds.get("formula_error") or kinds.get("special_value"):
        flags.append("contains formula errors / NaN special values")
    # Links: values that are Airtable record IDs.
    rec_atoms = [a for a in atoms if isinstance(a, str) and REC_ID.match(a)]
    if rec_atoms:
        targets = Counter(id_index.get(a, "<unresolved>") for a in rec_atoms)
        per_rec = [len(v) if isinstance(v, list) else 1 for v in present]
        f["links_to"] = dict(targets)
        f["link_cardinality"] = {"max_per_record": max(per_rec),
                                 "records_with_multiple": sum(1 for c in per_rec if c > 1)}
        if targets.get("<unresolved>"):
            flags.append(f"{targets['<unresolved>']} linked IDs do not resolve to any cached record")
    # Numbers and dates: range.
    nums = [a for a in atoms if isinstance(a, (int, float)) and not isinstance(a, bool)]
    if nums:
        f["numeric"] = {"min": min(nums), "median": statistics.median(nums), "max": max(nums)}
    dates = sorted(a for a in atoms if isinstance(a, str) and ISO_DATE.match(a))
    if dates:
        f["date_range"] = [dates[0], dates[-1]]
    # Categories: declared selects, or low-cardinality repeated values.
    is_select = bool(meta and meta["type"] in ("singleSelect", "multipleSelects", "checkbox"))
    if not rec_atoms and distinct and (is_select or (len(distinct) <= MAX_CATEGORIES and len(atoms) >= 2 * len(distinct))):
        f["value_counts"] = dict(distinct.most_common())
        f["value_counts"]["(empty)"] = n - len(present)
    f["flags"] = flags
    return f


def text_key_links(data, profiles):
    """Find text fields whose values match another table's (near-)unique text field."""
    keys = {}
    for t, recs in data.items():
        for fp in profiles[t]["fields"]:
            vals = [r["fields"].get(fp["field"]) for r in recs]
            vals = [v for v in vals if isinstance(v, str) and v]
            if len(vals) >= 5 and len(set(vals)) / len(vals) >= KEY_UNIQUENESS:
                keys[(t, fp["field"])] = set(vals)
    found = []
    for t, recs in data.items():
        for fp in profiles[t]["fields"]:
            vals = [r["fields"].get(fp["field"]) for r in recs]
            vals = [v for v in vals if isinstance(v, str) and v and not REC_ID.match(v)]
            if not vals:
                continue
            for (kt, kf), kv in keys.items():
                if kt == t:
                    continue
                hit = sum(v in kv for v in vals) / len(vals)
                if hit >= FK_OVERLAP:
                    found.append({"from": f"{t}.{fp['field']}", "to": f"{kt}.{kf}", "match_rate": round(hit, 4)})
    return [f"{t}.{f}" for t, f in keys], found


def main():
    manifest, schema, data = load()
    meta_by_table = {t["name"]: {f["name"]: f for f in t["fields"]} for t in (schema or [])}
    primary = {t["name"]: next((f["name"] for f in t["fields"] if f["id"] == t["primaryFieldId"]), None)
               for t in (schema or [])}
    id_index = {r["id"]: t for t, recs in data.items() for r in recs}

    profiles = {}
    for t, recs in data.items():
        n = len(recs)
        meta = meta_by_table.get(t, {})
        seen = list(dict.fromkeys(k for r in recs for k in r["fields"]))
        names = list(meta) + [k for k in seen if k not in meta]
        created = sorted(r["createdTime"] for r in recs)
        profiles[t] = {
            "table": t, "record_count": n, "pages": manifest["tables"][t]["pages"],
            "primary_field": primary.get(t),
            "created_time_range": [created[0], created[-1]] if created else None,
            "fields_declared_but_never_filled": [k for k in meta if k not in seen],
            "fields_seen_but_not_declared": [k for k in seen if meta and k not in meta],
            "fields": [profile_field(k, meta.get(k), [r["fields"].get(k) for r in recs], n, id_index)
                       for k in names],
        }
    candidate_keys, text_links = text_key_links(data, profiles)
    result = {"fetched_at": manifest["fetched_at"], "schema_source": "meta API" if schema else "inferred",
              "tables": profiles, "candidate_text_keys": candidate_keys, "text_key_links": text_links}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "profile.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    (OUT / "profile.md").write_text(render(result))
    print(f"Wrote {OUT / 'profile.md'} and profile.json")


def render(p):
    L = [f"# Data profile\n", f"Fetched {p['fetched_at']} · schema source: {p['schema_source']}\n",
         "| Table | Records | Fields | Pages |", "|---|---:|---:|---:|"]
    for t in p["tables"].values():
        L.append(f"| {t['table']} | {t['record_count']} | {len(t['fields'])} | {t['pages']} |")
    L.append("\n## Relationships\n")
    for t in p["tables"].values():
        for f in t["fields"]:
            if "links_to" in f:
                L.append(f"- `{t['table']}.{f['field']}` → {f['links_to']} "
                         f"(max {f['link_cardinality']['max_per_record']}/record, "
                         f"{f['link_cardinality']['records_with_multiple']} records with >1)")
    for l in p["text_key_links"]:
        L.append(f"- `{l['from']}` ≈ `{l['to']}` by value ({l['match_rate']:.0%} match)")
    L.append(f"\nCandidate text keys (≥{KEY_UNIQUENESS:.0%} unique): " +
             ", ".join(f"`{k}`" for k in p["candidate_text_keys"]))
    for t in p["tables"].values():
        L.append(f"\n## {t['table']} ({t['record_count']} records)\n")
        L.append(f"Primary field: `{t['primary_field']}` · createdTime {t['created_time_range']}")
        if t["fields_declared_but_never_filled"]:
            L.append(f"\nDeclared but empty in every record: {t['fields_declared_but_never_filled']}")
        L += ["", "| Field | Type | Missing | Distinct | Examples | Notes |", "|---|---|---:|---:|---|---|"]
        for f in t["fields"]:
            notes = []
            if "links_to" in f:
                notes.append("links → " + ", ".join(f["links_to"]))
            if "numeric" in f:
                notes.append("range {min}…{max}, median {median}".format(**f["numeric"]))
            if "date_range" in f:
                notes.append(f"{f['date_range'][0]} … {f['date_range'][1]}")
            notes += f["flags"]
            ex = "; ".join(e.replace("|", "\\|") for e in f["examples"])
            L.append(f"| {f['field']} | {f['type']} | {f['missing_rate']:.1%} | {f['distinct']} | {ex} | "
                     + "; ".join(notes).replace("|", "\\|") + " |")
        for f in t["fields"]:
            if "value_counts" in f:
                vc = ", ".join(f"{short(str(k), 40)}: {v}" for k, v in f["value_counts"].items())
                L.append(f"\n- **{f['field']}** values: {vc}")
                if "declared_choices" in f:
                    unused = set(f["declared_choices"]) - set(f["value_counts"])
                    if unused:
                        L.append(f"  - declared but unused choices: {sorted(unused)}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
