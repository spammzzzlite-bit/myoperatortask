"""D4 data-quality audit. Reads data/raw/ only; makes no API calls.

    python3 -m pipeline.audit

Writes outputs/data_quality.md (readable) and outputs/data_quality.csv (one row
per check). Each check states the records it applies to, the records that fail
it, up to five example record IDs, and which deliverable a failure could affect.

Outputs carry Airtable record IDs only, never names, emails or phones, so they
can be committed. A guard refuses to write if any candidate or staff name,
email or phone would appear in them.

Descriptive only: it counts problems and says where they are. It does not
decide what they mean for the VP's claims.
"""
import csv
import difflib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from pipeline.airtable import ROOT

RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"

# Deliverable tags
CH = "D1 channel"
ACC = "D1/D3 acceptance"
D2 = "D2"

CATEGORIES = {
    "U": "Uniqueness",
    "L": "Link integrity",
    "X": "Cross-field consistency",
    "T": "Date order and plausibility",
    "H": "Category and format hygiene",
    "M": "Missing values (D1/D3 inputs)",
    "O": "Other",
}

# Plausibility thresholds. Each one is repeated in the rule text of its check.
DECISION_GAP_DAYS = 30      # offer outstanding longer than this before a decision
STALE_PENDING_DAYS = 30     # pending offer older than this at fetch time
START_GAP_DAYS = 120        # decision to proposed start longer than this
APPLIED_TO_CLOSE_DAYS = 365
STALE_ACTIVE_DAYS = 90      # active application with no stage date this recent
NEAR_DUP_RATIO = 0.9        # difflib ratio for "possible misspelling" of a category value
TEMPLATE_MIN_RECORDS = 5    # free text shared verbatim by at least this many records
EXAMPLES = 5

DATE_FIELDS = {
    "People": ["Joined On"],
    "Job Openings": ["Opened On", "Target Close"],
    "Candidates": ["Created On"],
    "Applications": ["Applied On", "Screened On", "First Interview On", "Final Interview On",
                     "Offered On", "Closed On"],
    "Interviews": ["Scheduled On", "Completed On"],
    "Offers": ["Offered On", "Decision On", "Proposed Start Date"],
}
PLAN_DATES = {("Job Openings", "Target Close"), ("Offers", "Proposed Start Date")}  # may be in the future
BUSINESS_CREATED = {"People": "Joined On", "Job Openings": "Opened On", "Candidates": "Created On",
                    "Applications": "Applied On", "Interviews": "Scheduled On", "Offers": "Offered On"}

# (field, min, max) per table; None = unbounded
NUMERIC_FIELDS = {
    "Departments": [("Headcount Budget", 0, None)],
    "Job Openings": [("Salary Band Min", 1, None), ("Salary Band Max", 1, None), ("Headcount", 1, None)],
    "Candidates": [("Expected CTC", 1, None), ("Current CTC", 1, None),
                   ("Notice Period Days", 0, 365), ("Years Experience", 0, 50)],
    "Interviews": [("Score", 1, 5)],
    "Offers": [("Joining Bonus", 0, None), ("Base CTC", 1, None)],
}

ID_FIELDS = [  # table, field, expected format, affects
    ("Applications", "Application ID", r"APP-\d{5}", (CH, ACC)),
    ("Candidates", "Candidate ID", r"CAND-\d{5}", (CH,)),
    ("Offers", "Offer ID", r"OFF-\d{5}", (ACC,)),
    ("Interviews", "Interview ID", r"INT-\d{5}", (D2,)),
    ("Job Openings", "Req ID", r"REQ-\d{4}-\d{3}", (D2,)),
    ("Departments", "Code", r"[A-Z]{2,4}", (D2,)),
]
NATURAL_KEYS = [  # other fields that should be unique per record
    ("Candidates", "Email", (CH,)),
    ("People", "Work Email", (D2,)),
    ("People", "Full Name", (D2,)),
    ("Departments", "Name", (D2,)),
]

# Two-way link pairs: (table A, field on A, cardinality on A, table B, back-link field on B, cardinality on B, affects)
# Cardinality: "1" exactly one, "0..1" at most one, "1..*" at least one, "*" any.
LINK_PAIRS = [
    ("Applications", "Candidate", "1", "Candidates", "Applications", "1..*", (CH, ACC)),
    ("Applications", "Opening", "1", "Job Openings", "Applications", "*", (D2,)),
    ("Applications", "Recruiter", "1", "People", "Applications as Recruiter", "*", (D2,)),
    ("Applications", "Interviews", "*", "Interviews", "Application", "1", (D2,)),
    ("Applications", "Offers", "0..1", "Offers", "Application", "1", (ACC,)),
    ("Applications", "Referred By", "0..1", "People", "Referrals Made", "*", (CH,)),
    ("Job Openings", "Recruiter", "1", "People", "Reqs as Recruiter", "*", (D2,)),
    ("Job Openings", "Hiring Manager", "1", "People", "Reqs as Hiring Manager", "*", (D2,)),
    ("Job Openings", "Department", "1", "Departments", "Job Openings", "*", (D2,)),
    ("People", "Department", "1", "Departments", "People", "*", (D2,)),
    ("Interviews", "Interviewer", "1", "People", "Interviews", "*", (D2,)),
]
LINK_ROLES = [  # link field whose target person should hold one of these People.Role values
    ("Applications", "Recruiter", {"Recruiter"}, (D2,)),
    ("Job Openings", "Recruiter", {"Recruiter"}, (D2,)),
    ("Job Openings", "Hiring Manager", {"Hiring Manager"}, (D2,)),
    ("Interviews", "Interviewer", {"Interviewer", "Hiring Manager"}, (D2,)),
]

# Fields that are free text, identifiers or personal data: never treated as categories.
NOT_CATEGORY = {("Candidates", "Notes"), ("Interviews", "Feedback"), ("Candidates", "Full Name"),
                ("People", "Full Name"), ("People", "Work Email"), ("Candidates", "Email"),
                ("Candidates", "Phone")} | {(t, f) for t, f, _, _ in ID_FIELDS}
MAX_CATEGORY_VALUES = 25

MISSING_FIELDS = [  # non-link fields that D1/D3 read on every record (links are covered by L checks)
    ("Candidates", "Source", (CH,)),
    ("Candidates", "Created On", (CH,)),
    ("Applications", "Stage", (CH, ACC)),
    ("Applications", "Status", (CH, ACC)),
    ("Applications", "Applied On", (CH, ACC)),
    ("Offers", "Status", (ACC,)),
    ("Offers", "Offered On", (ACC,)),
    ("Offers", "Base CTC", (ACC,)),
    ("Offers", "Proposed Start Date", (ACC,)),
    ("Job Openings", "Status", (CH, D2)),
    ("Job Openings", "Headcount", (CH, D2)),
]

TERMINAL_STAGES = {"Rejected", "Withdrawn", "Hired"}
OPEN_STAGES = {"Applied", "Screening", "Interview", "Offer"}
PRE_OFFER_STAGES = {"Applied", "Screening", "Interview"}
POSITIVE_REC = {"Hire", "Strong Hire"}
NEGATIVE_REC = {"No Hire", "Strong No Hire"}
# Interview feedback is a small set of stock sentences; classify by phrase.
POSITIVE_FEEDBACK = ("solid fundamentals", "excellent depth", "good practical experience",
                     "clear communicator", "no red flags", "would hire")
NEGATIVE_FEEDBACK = ("missed the main edge case", "could not", "surface level", "not convinced",
                     "significant gaps", "struggled")
INBOUND_SOURCES = {"Job Board", "Career Site"}
SENIORITY_PREFIX = {"Junior": "Junior", "Lead": "Lead", "Senior": "Senior"}


@dataclass
class Check:
    id: str
    category: str
    name: str
    table: str
    field: str
    affects: tuple
    rule: str
    checked: list
    failing: list
    in_union: bool = True   # False for field-level properties that would swamp the per-record totals

    @property
    def pct(self):
        return 100 * len(self.failing) / len(self.checked) if self.checked else None

    @property
    def examples(self):
        return self.failing[:EXAMPLES]


def d(v):
    """ISO date string -> date, else None."""
    if not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def links(f, k):
    v = f.get(k)
    return v if isinstance(v, list) else []


def one(f, k):
    v = links(f, k)
    return v[0] if len(v) == 1 else None


def empty(v):
    return v is None or v == "" or v == []


def norm(s):
    return " ".join(s.split()).casefold()


class Audit:
    def __init__(self, records, as_of):
        """records: {table: [raw Airtable record]}; as_of: date of the fetch."""
        self.t = {name: {r["id"]: r.get("fields", {}) for r in recs} for name, recs in records.items()}
        self.created = {name: {r["id"]: r.get("createdTime") for r in recs} for name, recs in records.items()}
        for name in DATE_FIELDS:
            self.t.setdefault(name, {})
        for name in ("Departments", "Findings"):
            self.t.setdefault(name, {})
        self.as_of = as_of
        self.checks = []
        self._seq = Counter()
        self._index()

    # ---- helpers -----------------------------------------------------------------

    def _index(self):
        A, O, I = self.t["Applications"], self.t["Offers"], self.t["Interviews"]
        self.apps_by_cand = defaultdict(list)
        for aid, f in A.items():
            c = one(f, "Candidate")
            if c:
                self.apps_by_cand[c].append(aid)
        self.offer_of_app = defaultdict(list)       # from the Offers side
        for oid, f in O.items():
            a = one(f, "Application")
            if a:
                self.offer_of_app[a].append(oid)
        self.ivs_of_app = defaultdict(list)         # from the Interviews side
        for iid, f in I.items():
            a = one(f, "Application")
            if a:
                self.ivs_of_app[a].append(iid)

    def add(self, cat, name, table, field, affects, rule, checked, failing, in_union=True):
        checked, failing = set(checked), set(failing)
        assert failing <= checked, f"{name}: failing records not in checked set"
        self._seq[cat] += 1
        self.checks.append(Check(f"{cat}{self._seq[cat]:02d}", cat, name, table, field, tuple(affects), rule,
                                 sorted(checked), sorted(failing), in_union))

    def order(self, cat, name, table, field, affects, rule, items):
        """items: (record id, earlier date, later date). Checked when both dates parse; fails if later < earlier."""
        checked, failing = [], []
        for rid, a, b in items:
            if a and b:
                checked.append(rid)
                if b < a:
                    failing.append(rid)
        self.add(cat, name, table, field, affects, rule, checked, failing)

    def gap(self, name, table, field, affects, rule, items, max_days):
        """items: (record id, start, end). Fails if end - start exceeds max_days."""
        checked, failing = [], []
        for rid, a, b in items:
            if a and b:
                checked.append(rid)
                if (b - a).days > max_days:
                    failing.append(rid)
        self.add("T", name, table, field, affects, rule, checked, failing)

    def app_date(self, aid, k):
        return d(self.t["Applications"].get(aid, {}).get(k))

    def latest_interview(self, aid):
        """Latest known interview date for an application, from its own fields and linked interviews."""
        f = self.t["Applications"].get(aid, {})
        ds = [d(f.get("First Interview On")), d(f.get("Final Interview On"))]
        ds += [d(self.t["Interviews"][i].get("Scheduled On")) for i in links(f, "Interviews") if i in self.t["Interviews"]]
        ds = [x for x in ds if x]
        return max(ds) if ds else None

    def hires_by_opening(self):
        n = Counter()
        for f in self.t["Applications"].values():
            if f.get("Stage") == "Hired" and one(f, "Opening"):
                n[one(f, "Opening")] += 1
        return n

    # ---- 1. Uniqueness -------------------------------------------------------------

    def uniqueness(self):
        for table, fld, pattern, affects in ID_FIELDS:
            recs = self.t[table]
            self._dup_check(table, fld, affects, f"`{fld}` shared by more than one record (after trimming and casefolding)")
            bad = [r for r, f in recs.items() if not isinstance(f.get(fld), str) or not re.fullmatch(pattern, f[fld])]
            self.add("U", f"{fld} missing or not in expected format", table, fld, affects,
                     f"`{fld}` missing or not matching `{pattern}`", recs, bad)
        for table, fld, affects in NATURAL_KEYS:
            self._dup_check(table, fld, affects, f"`{fld}` shared by more than one record (after trimming and casefolding)")

        A, C, O, I = self.t["Applications"], self.t["Candidates"], self.t["Offers"], self.t["Interviews"]
        key = {a: (one(f, "Candidate"), one(f, "Opening")) for a, f in A.items() if one(f, "Candidate") and one(f, "Opening")}
        self.add("U", "Same candidate applied to the same opening more than once", "Applications", "Candidate+Opening",
                 (CH, ACC), "More than one application with the same Candidate and Opening links",
                 key, self._members_of_dups(key))

        name = {c: norm(f["Full Name"]) for c, f in C.items() if isinstance(f.get("Full Name"), str)}
        phone = {c: re.sub(r"\D", "", f["Phone"]) for c, f in C.items() if isinstance(f.get("Phone"), str)}
        email = {c: norm(f["Email"]) for c, f in C.items() if isinstance(f.get("Email"), str)}
        both = [c for c in C if c in name and c in phone]
        groups = defaultdict(list)
        for c in both:
            groups[(name[c], phone[c])].append(c)
        fail = [c for g in groups.values() if len(g) > 1 and len({email.get(x) for x in g}) > 1 for c in g]
        self.add("U", "Likely duplicate candidate: same name and phone, different email", "Candidates",
                 "Full Name+Phone+Email", (CH, ACC),
                 "Candidates whose normalised name and phone digits match another candidate but whose email differs; "
                 "all records in each group are counted", both, fail)
        groups = defaultdict(list)
        for c in phone:
            groups[phone[c]].append(c)
        fail = [c for g in groups.values() if len(g) > 1 and len({name.get(x) for x in g}) > 1 for c in g]
        self.add("U", "Same phone number on candidates with different names", "Candidates", "Phone", (CH,),
                 "Phone digits shared by candidates whose names differ", phone, fail)
        groups = defaultdict(list)
        for c in name:
            groups[name[c]].append(c)
        fail = [c for g in groups.values() if len(g) > 1
                and len({phone.get(x) for x in g}) == len(g) and len({email.get(x) for x in g}) == len(g) for c in g]
        self.add("U", "Same name, different phone and email (namesakes or undetected duplicates)", "Candidates",
                 "Full Name", (CH,), "Normalised name shared by candidates with all-distinct phones and emails",
                 name, fail)

        hired = defaultdict(int)
        for a, f in A.items():
            if f.get("Stage") == "Hired" and one(f, "Candidate"):
                hired[one(f, "Candidate")] += 1
        self.add("U", "Candidate has more than one Hired application", "Candidates", "Applications.Stage", (CH,),
                 "Candidates with ≥1 application at Stage=Hired; fails if more than one", hired,
                 [c for c, n in hired.items() if n > 1])
        acc = defaultdict(int)
        for o, f in O.items():
            a = one(f, "Application")
            c = one(A.get(a, {}), "Candidate") if a else None
            if f.get("Status") == "Accepted" and c:
                acc[c] += 1
        self.add("U", "Candidate has more than one Accepted offer", "Candidates", "Offers.Status", (ACC,),
                 "Candidates with ≥1 Accepted offer (via Offers.Application → Applications.Candidate); fails if more than one",
                 acc, [c for c, n in acc.items() if n > 1])
        key = {i: (one(f, "Application"), f.get("Round")) for i, f in I.items() if one(f, "Application") and f.get("Round")}
        self.add("U", "Same interview round recorded more than once for an application", "Interviews",
                 "Application+Round", (D2,),
                 "More than one interview with the same Application and Round (can be a legitimate reschedule; "
                 "see Outcome)", key, self._members_of_dups(key))

    def _dup_check(self, table, fld, affects, rule):
        vals = {r: norm(f[fld]) for r, f in self.t[table].items() if isinstance(f.get(fld), str) and f[fld].strip()}
        self.add("U", f"Duplicate {fld}", table, fld, affects, rule, vals, self._members_of_dups(vals))

    @staticmethod
    def _members_of_dups(keyed):
        groups = defaultdict(list)
        for r, k in keyed.items():
            groups[k].append(r)
        return [r for g in groups.values() if len(g) > 1 for r in g]

    # ---- 2. Link integrity ---------------------------------------------------------

    def link_integrity(self):
        for a, fa, ca, b, fb, cb, affects in LINK_PAIRS:
            for src, sf, card, dst, df in ((a, fa, ca, b, fb), (b, fb, cb, a, fa)):
                S, D = self.t[src], self.t[dst]
                checked = [r for r, f in S.items() if links(f, sf)]
                bad = [r for r in checked
                       if any(x not in D or r not in links(D[x], df) for x in links(S[r], sf))]
                self.add("L", f"{src}.{sf} → {dst}: target exists and links back", src, sf, affects,
                         f"Every record ID in `{src}.{sf}` exists in {dst} and that record's `{df}` contains the source record",
                         checked, bad)
                if card != "*":
                    rule = {"1": "exactly one", "0..1": "at most one", "1..*": "at least one"}[card]
                    test = {"1": lambda n: n != 1, "0..1": lambda n: n > 1, "1..*": lambda n: n == 0}[card]
                    self.add("L", f"{src}.{sf} should hold {rule} link", src, sf, affects,
                             f"`{src}.{sf}` must hold {rule} linked record", S,
                             [r for r, f in S.items() if test(len(links(f, sf)))])
        P = self.t["People"]
        for table, fld, roles, affects in LINK_ROLES:
            S = self.t[table]
            checked = [r for r, f in S.items() if one(f, fld) in P]
            bad = [r for r in checked if P[one(S[r], fld)].get("Role") not in roles]
            self.add("L", f"{table}.{fld} points to a person without the expected role", table, fld, affects,
                     f"Linked person's `People.Role` not in {sorted(roles)}", checked, bad)

    # ---- 3. Cross-field consistency -----------------------------------------------

    def cross_field(self):
        A, O, I, J, C, P = (self.t[k] for k in ("Applications", "Offers", "Interviews", "Job Openings", "Candidates", "People"))

        # Stage / Status / Closed On / Rejection Reason
        chk = [a for a, f in A.items() if f.get("Stage") in TERMINAL_STAGES | OPEN_STAGES and f.get("Status")]
        bad = [a for a in chk if (A[a]["Stage"] in TERMINAL_STAGES) != (A[a]["Status"] == "Closed")]
        self.add("X", "Stage disagrees with Status", "Applications", "Stage vs Status", (CH, ACC),
                 f"Stage in {sorted(TERMINAL_STAGES)} should have Status=Closed; Stage in {sorted(OPEN_STAGES)} "
                 "should have Status=Active", chk, bad)
        chk = [a for a, f in A.items() if f.get("Status") in ("Closed", "Active")]
        bad = [a for a in chk if (A[a]["Status"] == "Closed") != (not empty(A[a].get("Closed On")))]
        self.add("X", "Status disagrees with Closed On", "Applications", "Status vs Closed On", (CH, ACC),
                 "Status=Closed must have Closed On; Status=Active must not", chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") and f["Stage"] not in ("Rejected", "Withdrawn")]
        bad = [a for a in chk if not empty(A[a].get("Rejection Reason"))]
        self.add("X", "Rejection Reason set on an application that was not rejected or withdrawn", "Applications",
                 "Stage vs Rejection Reason", (CH, ACC),
                 "Stage not in [Rejected, Withdrawn] but Rejection Reason is filled", chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") == "Rejected"]
        bad = [a for a in chk if empty(A[a].get("Rejection Reason"))]
        self.add("X", "Rejected application has no Rejection Reason", "Applications", "Rejection Reason", (D2,),
                 "Stage=Rejected with Rejection Reason empty", chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") == "Withdrawn" or f.get("Rejection Reason") == "Withdrew"]
        bad = [a for a in chk if (A[a].get("Stage") == "Withdrawn") != (A[a].get("Rejection Reason") == "Withdrew")]
        self.add("X", "Withdrawn stage and 'Withdrew' reason disagree", "Applications", "Stage vs Rejection Reason",
                 (ACC,), "Stage=Withdrawn should carry Rejection Reason=Withdrew, and vice versa", chk, bad)
        chk = [a for a, f in A.items() if f.get("Rejection Reason") == "Position Cancelled" and one(f, "Opening") in J]
        bad = [a for a in chk if J[one(A[a], "Opening")].get("Status") != "Cancelled"]
        self.add("X", "'Position Cancelled' rejection on an opening that is not Cancelled", "Applications",
                 "Rejection Reason vs Job Openings.Status", (D2,),
                 "Rejection Reason=Position Cancelled but the linked opening's Status is not Cancelled", chk, bad)
        chk = [a for a, f in A.items() if f.get("Status") == "Active" and one(f, "Opening") in J]
        bad = [a for a in chk if J[one(A[a], "Opening")].get("Status") in ("Cancelled", "Filled")]
        self.add("X", "Active application on a Cancelled or Filled opening", "Applications",
                 "Status vs Job Openings.Status", (D2,),
                 "Status=Active while the linked opening's Status is Cancelled or Filled", chk, bad)

        # Hire vs offer
        chk = [a for a, f in A.items() if f.get("Stage") == "Hired"]
        bad = [a for a in chk if not any(O.get(o, {}).get("Status") == "Accepted" for o in links(A[a], "Offers"))]
        self.add("X", "Hired application without an Accepted offer", "Applications", "Stage vs Offers.Status",
                 (CH, ACC), "Stage=Hired but no linked offer has Status=Accepted (includes no offer at all)", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") == "Accepted" and one(f, "Application") in A]
        bad = [o for o in chk if A[one(O[o], "Application")].get("Stage") != "Hired"]
        self.add("X", "Accepted offer whose application is not Hired", "Offers", "Status vs Applications.Stage",
                 (CH, ACC), "Offer Status=Accepted but the linked application's Stage is not Hired", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") == "Pending" and one(f, "Application") in A]
        bad = [o for o in chk if A[one(O[o], "Application")].get("Stage") != "Offer"
               or A[one(O[o], "Application")].get("Status") != "Active"]
        self.add("X", "Pending offer whose application is not an active Offer-stage application", "Offers",
                 "Status vs Applications.Stage/Status", (ACC,),
                 "Offer Status=Pending but the application is not Stage=Offer with Status=Active", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") == "Declined" and one(f, "Application") in A]
        bad = [o for o in chk if A[one(O[o], "Application")].get("Status") == "Active"
               or A[one(O[o], "Application")].get("Stage") in ("Offer", "Hired")]
        self.add("X", "Declined offer whose application is still open or Hired", "Offers",
                 "Status vs Applications.Stage/Status", (ACC,),
                 "Offer Status=Declined but the application is Active, or at Stage Offer or Hired", chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") == "Offer"]
        bad = [a for a in chk if not links(A[a], "Offers")]
        self.add("X", "Offer-stage application with no offer record", "Applications", "Stage vs Offers", (ACC,),
                 "Stage=Offer but Applications.Offers is empty", chk, bad)
        chk = [a for a, f in A.items() if links(f, "Offers")]
        bad = [a for a in chk if A[a].get("Stage") in PRE_OFFER_STAGES]
        self.add("X", "Offer record on an application still at a pre-offer stage", "Applications", "Stage vs Offers",
                 (ACC,), f"Application has an offer but Stage is in {sorted(PRE_OFFER_STAGES)}", chk, bad)

        # Offer status vs decision fields
        chk = [o for o, f in O.items() if f.get("Status") == "Pending"]
        bad = [o for o in chk if not empty(O[o].get("Decision On"))]
        self.add("X", "Pending offer has a Decision On date", "Offers", "Status vs Decision On", (ACC,),
                 "Status=Pending with Decision On filled", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") in ("Accepted", "Declined")]
        bad = [o for o in chk if empty(O[o].get("Decision On"))]
        self.add("X", "Decided offer has no Decision On date", "Offers", "Status vs Decision On", (ACC,),
                 "Status in [Accepted, Declined] with Decision On empty", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") == "Declined"]
        bad = [o for o in chk if empty(O[o].get("Decline Reason"))]
        self.add("X", "Declined offer has no Decline Reason", "Offers", "Decline Reason", (ACC,),
                 "Status=Declined with Decline Reason empty", chk, bad)
        chk = [o for o, f in O.items() if f.get("Status") and f["Status"] != "Declined"]
        bad = [o for o in chk if not empty(O[o].get("Decline Reason"))]
        self.add("X", "Decline Reason on an offer that was not declined", "Offers", "Decline Reason", (ACC,),
                 "Status other than Declined with Decline Reason filled", chk, bad)

        # The two copies of the offer date
        chk = [a for a, f in A.items() if links(f, "Offers") or not empty(f.get("Offered On"))]
        bad = [a for a in chk if bool(links(A[a], "Offers")) != (not empty(A[a].get("Offered On")))]
        self.add("X", "Applications.Offered On present without an offer, or offer without Applications.Offered On",
                 "Applications", "Offered On vs Offers", (ACC,),
                 "Applications.Offered On should be filled exactly when the application has an offer", chk, bad)
        chk = [a for a, f in A.items() if not empty(f.get("Offered On")) and one(f, "Offers") in O]
        bad = [a for a in chk if A[a]["Offered On"] != O[one(A[a], "Offers")].get("Offered On")]
        self.add("X", "Applications.Offered On differs from Offers.Offered On", "Applications",
                 "Offered On vs Offers.Offered On", (ACC,),
                 "Both copies present and not equal", chk, bad)

        # Interview dates vs the Interviews table
        chk = [a for a, f in A.items() if links(f, "Interviews") or not empty(f.get("First Interview On"))]
        bad = []
        for a in chk:
            sched = sorted(x for x in (d(I.get(i, {}).get("Scheduled On")) for i in links(A[a], "Interviews")) if x)
            first = self.app_date(a, "First Interview On")
            if not sched or first != sched[0]:
                bad.append(a)
        self.add("X", "First Interview On disagrees with the earliest linked interview", "Applications",
                 "First Interview On vs Interviews.Scheduled On", (D2,),
                 "First Interview On should equal the earliest Scheduled On of linked interviews "
                 "(fails if either side is missing)", chk, bad)
        chk = [a for a, f in A.items() if not empty(f.get("Final Interview On"))]
        bad = []
        for a in chk:
            sched = sorted(x for x in (d(I.get(i, {}).get("Scheduled On")) for i in links(A[a], "Interviews")) if x)
            if not sched or self.app_date(a, "Final Interview On") != sched[-1]:
                bad.append(a)
        self.add("X", "Final Interview On disagrees with the latest linked interview", "Applications",
                 "Final Interview On vs Interviews.Scheduled On", (D2,),
                 "Final Interview On should equal the latest Scheduled On of linked interviews "
                 "(fails if no interviews are linked)", chk, bad)
        chk = [a for a, f in A.items() if not empty(f.get("Final Interview On"))]
        bad = [a for a in chk if len(links(A[a], "Interviews")) < 2]
        self.add("X", "Final Interview On set but fewer than two interviews recorded", "Applications",
                 "Final Interview On vs Interviews", (D2,),
                 "A distinct final interview implies at least two interview records", chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") in ("Interview", "Offer", "Hired")]
        bad = [a for a in chk if not links(A[a], "Interviews")]
        self.add("X", "Interview-stage or later application with no interview records", "Applications",
                 "Stage vs Interviews", (D2, ACC), "Stage in [Interview, Offer, Hired] with no linked interviews",
                 chk, bad)
        chk = [a for a, f in A.items() if f.get("Stage") in ("Interview", "Offer", "Hired")]
        bad = [a for a in chk if empty(A[a].get("Screened On"))]
        self.add("X", "Interview-stage or later application with no Screened On date", "Applications",
                 "Stage vs Screened On", (D2,), "Stage in [Interview, Offer, Hired] with Screened On empty", chk, bad)

        # Interview record internals
        chk = [i for i, f in I.items() if f.get("Outcome")]
        bad = []
        for i in chk:
            f = I[i]
            filled = [not empty(f.get(k)) for k in ("Completed On", "Score", "Recommendation", "Feedback")]
            if (f["Outcome"] == "Completed" and not all(filled)) or (f["Outcome"] != "Completed" and any(filled)):
                bad.append(i)
        self.add("X", "Interview Outcome disagrees with its result fields", "Interviews",
                 "Outcome vs Completed On/Score/Recommendation/Feedback", (D2,),
                 "Outcome=Completed needs all four result fields; any other Outcome should have none", chk, bad)
        chk = [i for i, f in I.items() if isinstance(f.get("Score"), (int, float))
               and f.get("Recommendation") in POSITIVE_REC | NEGATIVE_REC]
        bad = [i for i in chk if (I[i]["Recommendation"] in POSITIVE_REC and I[i]["Score"] <= 2)
               or (I[i]["Recommendation"] in NEGATIVE_REC and I[i]["Score"] >= 4)]
        self.add("X", "Interview Score contradicts Recommendation", "Interviews", "Score vs Recommendation", (D2,),
                 "Hire/Strong Hire with Score ≤ 2, or No Hire/Strong No Hire with Score ≥ 4 (1–5 scale)", chk, bad)
        chk, bad = [], []
        for i, f in I.items():
            fb = norm(f.get("Feedback") or "")
            pos, neg = any(p in fb for p in POSITIVE_FEEDBACK), any(p in fb for p in NEGATIVE_FEEDBACK)
            if pos == neg or f.get("Recommendation") not in POSITIVE_REC | NEGATIVE_REC:
                continue    # unclassifiable feedback or no recommendation
            chk.append(i)
            if pos != (f["Recommendation"] in POSITIVE_REC):
                bad.append(i)
        self.add("X", "Interview Feedback text contradicts Recommendation", "Interviews",
                 "Feedback vs Recommendation", (D2,),
                 "Feedback classified positive/negative by stock phrase; fails when its direction is opposite "
                 "to the Recommendation", chk, bad)

        # Openings: status and headcount vs hires
        hires = self.hires_by_opening()
        chk = [j for j, f in J.items() if f.get("Status") == "Filled" and isinstance(f.get("Headcount"), (int, float))]
        bad = [j for j in chk if hires[j] != J[j]["Headcount"]]
        self.add("X", "Filled opening whose hires ≠ Headcount", "Job Openings", "Status/Headcount vs hires", (CH, D2),
                 "Status=Filled but the number of linked applications at Stage=Hired differs from Headcount", chk, bad)
        chk = [j for j, f in J.items() if isinstance(f.get("Headcount"), (int, float))]
        bad = [j for j in chk if hires[j] > J[j]["Headcount"]]
        self.add("X", "More hires than Headcount", "Job Openings", "Headcount vs hires", (CH, D2),
                 "Linked applications at Stage=Hired exceed Headcount", chk, bad)
        chk = [j for j, f in J.items() if f.get("Status") in ("Open", "On Hold", "Cancelled")
               and isinstance(f.get("Headcount"), (int, float))]
        bad = [j for j in chk if (J[j]["Status"] == "Cancelled" and hires[j] > 0) or hires[j] >= J[j]["Headcount"]]
        self.add("X", "Open/On Hold opening already at Headcount, or Cancelled opening with hires", "Job Openings",
                 "Status vs hires", (CH, D2),
                 "Open or On Hold with hires ≥ Headcount, or Cancelled with any hire", chk, bad)

        # Compensation
        chk, bad = [], []
        for o, f in O.items():
            j = J.get(one(A.get(one(f, "Application"), {}), "Opening"))
            if j and all(isinstance(x, (int, float)) for x in (f.get("Base CTC"), j.get("Salary Band Min"), j.get("Salary Band Max"))):
                chk.append(o)
                if not j["Salary Band Min"] <= f["Base CTC"] <= j["Salary Band Max"]:
                    bad.append(o)
        self.add("X", "Offer Base CTC outside the opening's salary band", "Offers", "Base CTC vs Salary Band", (ACC,),
                 "Base CTC < Salary Band Min or > Salary Band Max of the linked opening", chk, bad)
        chk = [j for j, f in J.items() if isinstance(f.get("Salary Band Min"), (int, float))
               and isinstance(f.get("Salary Band Max"), (int, float))]
        bad = [j for j in chk if J[j]["Salary Band Min"] > J[j]["Salary Band Max"]]
        self.add("X", "Salary Band Min above Max", "Job Openings", "Salary Band Min/Max", (ACC,),
                 "Salary Band Min > Salary Band Max", chk, bad)

        # Source vs Referred By vs Notes
        chk = [a for a, f in A.items() if links(f, "Referred By") and one(f, "Candidate") in C]
        bad = [a for a in chk if C[one(A[a], "Candidate")].get("Source") != "Referral"]
        self.add("X", "Application has a referrer but candidate Source is not Referral", "Applications",
                 "Referred By vs Candidates.Source", (CH,),
                 "Referred By filled while the candidate's Source ≠ Referral", chk, bad)
        chk = [c for c, f in C.items() if f.get("Source") == "Referral"]
        bad = [c for c in chk if not any(links(A.get(a, {}), "Referred By") for a in links(C[c], "Applications"))]
        self.add("X", "Referral-sourced candidate with no referrer on any application", "Candidates",
                 "Source vs Applications.Referred By", (CH,),
                 "Source=Referral but none of the candidate's applications has Referred By", chk, bad)
        chk = [c for c, f in C.items() if "referred internally" in norm(f.get("Notes") or "")]
        bad = [c for c in chk if C[c].get("Source") != "Referral"]
        self.add("X", "Notes say 'Referred internally' but Source is not Referral", "Candidates", "Notes vs Source",
                 (CH,), "Notes contain 'Referred internally' while Source ≠ Referral", chk, bad)
        chk = [c for c, f in C.items() if "conference list" in norm(f.get("Notes") or "")]
        bad = [c for c in chk if C[c].get("Source") in INBOUND_SOURCES]
        self.add("X", "Notes say 'Sourced from a conference list' but Source is an inbound channel", "Candidates",
                 "Notes vs Source", (CH,),
                 f"Notes mention a conference list while Source is in {sorted(INBOUND_SOURCES)}", chk, bad)
        chk = [c for c, f in C.items() if "consolidated onto" in norm(f.get("Notes") or "")]
        bad = [c for c in chk if len(links(C[c], "Applications")) != 1]
        self.add("X", "Notes say applications were consolidated but the candidate does not have exactly one",
                 "Candidates", "Notes vs Applications", (CH, ACC),
                 "Notes contain 'consolidated onto' while the candidate has ≠ 1 application", chk, bad)
        chk = [c for c, f in C.items() if "earlier rejection" in norm(f.get("Notes") or "")]
        bad = []
        for c in chk:
            apps = [A[a] for a in links(C[c], "Applications") if a in A]
            last = max((d(f.get("Applied On")) for f in apps if d(f.get("Applied On"))), default=None)
            if not any(f.get("Stage") == "Rejected" and d(f.get("Applied On")) and last and d(f["Applied On"]) < last
                       for f in apps):
                bad.append(c)
        self.add("X", "Notes mention an earlier rejection that the data does not show", "Candidates",
                 "Notes vs Applications", (CH,),
                 "Notes contain 'earlier rejection' but the candidate has no Rejected application applied before "
                 "their latest one", chk, bad)

    # ---- 4. Date order and plausibility --------------------------------------------

    def dates(self):
        A, O, I, J, C, P = (self.t[k] for k in ("Applications", "Offers", "Interviews", "Job Openings", "Candidates", "People"))
        ad = self.app_date
        cand_created = lambda a: d(C.get(one(A[a], "Candidate"), {}).get("Created On"))
        opened = lambda a: d(J.get(one(A[a], "Opening"), {}).get("Opened On"))

        self.order("T", "Candidate created after applying", "Applications", "Candidates.Created On ≤ Applied On",
                   (CH,), "Candidate Created On is later than the application's Applied On",
                   ((a, cand_created(a), ad(a, "Applied On")) for a in A))
        self.order("T", "Applied before the opening was opened", "Applications", "Job Openings.Opened On ≤ Applied On",
                   (D2,), "Applied On is earlier than the linked opening's Opened On",
                   ((a, opened(a), ad(a, "Applied On")) for a in A))
        pairs = [("Applied On", "Screened On"), ("Screened On", "First Interview On"),
                 ("First Interview On", "Final Interview On"), ("Applied On", "Offered On"),
                 ("Applied On", "Closed On")]
        for x, y in pairs:
            self.order("T", f"{y} before {x}", "Applications", f"{x} ≤ {y}", (D2, ACC) if "Offered" in y else (D2,),
                       f"{y} is earlier than {x}", ((a, ad(a, x), ad(a, y)) for a in A))
        self.order("T", "Offered before the last interview", "Applications", "latest interview ≤ Offered On",
                   (ACC,), "Applications.Offered On earlier than the latest of First/Final Interview On and linked "
                   "Interviews.Scheduled On", ((a, self.latest_interview(a), ad(a, "Offered On")) for a in A))
        stage_dates = ("Applied On", "Screened On", "First Interview On", "Final Interview On", "Offered On")
        self.order("T", "Closed before a later stage event", "Applications", "Closed On ≥ every stage date", (D2, ACC),
                   "Closed On earlier than the latest of Applied/Screened/First Interview/Final Interview/Offered On",
                   ((a, max([x for x in (ad(a, k) for k in stage_dates) if x], default=None), ad(a, "Closed On"))
                    for a in A))
        rec_joined = lambda a: d(P.get(one(A[a], "Recruiter"), {}).get("Joined On"))
        self.order("T", "Recruiter joined Acme after the application", "Applications", "People.Joined On ≤ Applied On",
                   (D2,), "Assigned recruiter's Joined On is later than Applied On",
                   ((a, rec_joined(a), ad(a, "Applied On")) for a in A))

        iv_app = lambda i: one(I[i], "Application")
        self.order("T", "Interview scheduled before the application", "Interviews", "Applied On ≤ Scheduled On", (D2,),
                   "Scheduled On earlier than the application's Applied On",
                   ((i, ad(iv_app(i), "Applied On") if iv_app(i) else None, d(I[i].get("Scheduled On"))) for i in I))
        self.order("T", "Interview scheduled after the application closed", "Interviews", "Scheduled On ≤ Closed On",
                   (D2,), "Scheduled On later than the application's Closed On",
                   ((i, d(I[i].get("Scheduled On")), ad(iv_app(i), "Closed On") if iv_app(i) else None) for i in I))
        self.order("T", "Interview completed before it was scheduled", "Interviews", "Scheduled On ≤ Completed On",
                   (D2,), "Completed On earlier than Scheduled On",
                   ((i, d(I[i].get("Scheduled On")), d(I[i].get("Completed On"))) for i in I))
        self.order("T", "Interviewer joined Acme after the interview", "Interviews", "People.Joined On ≤ Scheduled On",
                   (D2,), "Interviewer's Joined On later than Scheduled On",
                   ((i, d(P.get(one(I[i], "Interviewer"), {}).get("Joined On")), d(I[i].get("Scheduled On"))) for i in I))

        off_app = lambda o: one(O[o], "Application")
        self.order("T", "Offer made before the last interview", "Offers", "latest interview ≤ Offers.Offered On", (ACC,),
                   "Offers.Offered On earlier than the application's latest interview date",
                   ((o, self.latest_interview(off_app(o)) if off_app(o) else None, d(O[o].get("Offered On"))) for o in O))
        self.order("T", "Decision before the offer", "Offers", "Offered On ≤ Decision On", (ACC,),
                   "Decision On earlier than Offered On", ((o, d(O[o].get("Offered On")), d(O[o].get("Decision On"))) for o in O))
        self.order("T", "Proposed start before the decision", "Offers", "Decision On ≤ Proposed Start Date", (ACC,),
                   "Proposed Start Date earlier than Decision On",
                   ((o, d(O[o].get("Decision On")), d(O[o].get("Proposed Start Date"))) for o in O))
        self.order("T", "Proposed start before the offer", "Offers", "Offered On ≤ Proposed Start Date", (ACC,),
                   "Proposed Start Date earlier than Offered On",
                   ((o, d(O[o].get("Offered On")), d(O[o].get("Proposed Start Date"))) for o in O))
        self.order("T", "Application closed before the offer decision", "Offers", "Decision On ≤ Applications.Closed On",
                   (ACC,), "The application's Closed On is earlier than the offer's Decision On",
                   ((o, d(O[o].get("Decision On")), ad(off_app(o), "Closed On") if off_app(o) else None) for o in O))
        self.order("T", "Target Close before Opened On", "Job Openings", "Opened On ≤ Target Close", (D2,),
                   "Target Close earlier than Opened On",
                   ((j, d(J[j].get("Opened On")), d(J[j].get("Target Close"))) for j in J))

        # Future dates, relative to the fetch date
        for table, fields in DATE_FIELDS.items():
            event = [f for f in fields if (table, f) not in PLAN_DATES]
            recs = self.t[table]
            chk = [r for r, f in recs.items() if any(d(f.get(k)) for k in event)]
            bad = [r for r in chk if any(d(recs[r].get(k)) and d(recs[r][k]) > self.as_of for k in event)]
            affects = (ACC,) if table == "Offers" else (CH,) if table == "Candidates" else (D2,)
            self.add("T", f"{table}: event date after the data was fetched", table, ", ".join(event), affects,
                     f"Any of {event} later than the fetch date {self.as_of}", chk, bad)
        chk = [i for i, f in I.items() if f.get("Outcome") == "Completed" and d(f.get("Scheduled On"))]
        bad = [i for i in chk if d(I[i]["Scheduled On"]) > self.as_of]
        self.add("T", "Completed interview scheduled in the future", "Interviews", "Outcome vs Scheduled On", (D2,),
                 f"Outcome=Completed with Scheduled On after {self.as_of}", chk, bad)

        # Implausible gaps
        self.gap(f"Offer outstanding more than {DECISION_GAP_DAYS} days before decision", "Offers",
                 "Offered On → Decision On", (ACC,), f"Decision On − Offered On > {DECISION_GAP_DAYS} days",
                 ((o, d(O[o].get("Offered On")), d(O[o].get("Decision On"))) for o in O), DECISION_GAP_DAYS)
        self.gap(f"Pending offer older than {STALE_PENDING_DAYS} days at fetch", "Offers", "Status=Pending, Offered On",
                 (ACC,), f"Status=Pending and fetch date − Offered On > {STALE_PENDING_DAYS} days",
                 ((o, d(O[o].get("Offered On")), self.as_of) for o in O if O[o].get("Status") == "Pending"),
                 STALE_PENDING_DAYS)
        self.gap(f"Proposed start more than {START_GAP_DAYS} days after decision", "Offers",
                 "Decision On → Proposed Start Date", (ACC,), f"Proposed Start Date − Decision On > {START_GAP_DAYS} days",
                 ((o, d(O[o].get("Decision On")), d(O[o].get("Proposed Start Date"))) for o in O), START_GAP_DAYS)
        self.gap(f"Application open more than {APPLIED_TO_CLOSE_DAYS} days", "Applications", "Applied On → Closed On",
                 (D2,), f"Closed On − Applied On > {APPLIED_TO_CLOSE_DAYS} days",
                 ((a, ad(a, "Applied On"), ad(a, "Closed On")) for a in A), APPLIED_TO_CLOSE_DAYS)
        self.gap(f"Active application with no stage activity in {STALE_ACTIVE_DAYS} days", "Applications",
                 "Status=Active, latest stage date", (D2, ACC),
                 f"Status=Active and fetch date − latest stage date > {STALE_ACTIVE_DAYS} days",
                 ((a, max([x for x in (ad(a, k) for k in stage_dates) if x], default=None), self.as_of)
                  for a in A if A[a].get("Status") == "Active"), STALE_ACTIVE_DAYS)

    # ---- 5. Category and format hygiene --------------------------------------------

    def hygiene(self):
        for table, recs in self.t.items():
            fields = sorted({k for f in recs.values() for k in f})
            for fld in fields:
                vals = {r: f[fld] for r, f in recs.items() if fld in f}
                if ((table, fld) in NOT_CATEGORY or fld in DATE_FIELDS.get(table, [])
                        or not vals or not all(isinstance(v, str) for v in vals.values())):
                    continue
                distinct = Counter(vals.values())
                if len(distinct) > MAX_CATEGORY_VALUES or len(distinct) > len(vals) / 2:
                    continue
                self._category_check(table, fld, vals, distinct)

        for table, fld, pattern, affects in [
                ("Candidates", "Email", r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", (CH,)),
                ("People", "Work Email", r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", (D2,)),
                ("Candidates", "Phone", r"\+91 [6-9]\d{9}", (CH,)),
                ("Candidates", "Full Name", r"\S+( \S+)+", (CH,)),
                ("People", "Full Name", r"\S+( \S+)+", (D2,))]:
            recs = self.t[table]
            chk = [r for r, f in recs.items() if isinstance(f.get(fld), str)]
            bad = [r for r in chk if not re.fullmatch(pattern, recs[r][fld])]
            self.add("H", f"{fld} not in expected format", table, fld, affects,
                     f"Value does not fully match `{pattern}` (catches stray whitespace, casing, malformed values)",
                     chk, bad)

        for table, fields in DATE_FIELDS.items():
            recs = self.t[table]
            chk = [r for r, f in recs.items() if any(k in f for k in fields)]
            bad = [r for r in chk if any(k in recs[r] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(recs[r][k]))
                                         or (k in recs[r] and d(recs[r][k]) is None) for k in fields)]
            self.add("H", f"{table}: unparseable date", table, ", ".join(fields), (D2,) if table not in ("Offers",) else (ACC,),
                     "Date field value is not a valid YYYY-MM-DD date", chk, bad)

        for table, specs in NUMERIC_FIELDS.items():
            recs = self.t[table]
            chk = [r for r, f in recs.items() if any(k in f for k, _, _ in specs)]
            bad = []
            for r in chk:
                for k, lo, hi in specs:
                    if k not in recs[r]:
                        continue
                    v = recs[r][k]
                    if (not isinstance(v, (int, float)) or isinstance(v, bool)
                            or (lo is not None and v < lo) or (hi is not None and v > hi)):
                        bad.append(r)
                        break
            desc = "; ".join(f"{k} in [{lo}, {hi if hi is not None else '∞'}]" for k, lo, hi in specs)
            self.add("H", f"{table}: non-numeric or out-of-range number", table, ", ".join(k for k, _, _ in specs),
                     (ACC,) if table == "Offers" else (D2,), f"Must be numeric with {desc}", chk, bad)

    def _category_check(self, table, fld, vals, distinct):
        by_key = defaultdict(Counter)
        words = {}
        for v, n in distinct.items():
            k = re.sub(r"[^a-z0-9]", "", v.casefold())
            by_key[k][v] += n
            words[k] = len(v.split())
        canonical = {}
        for group in by_key.values():
            top = group.most_common(1)[0][0]
            for v in group:
                canonical[v] = top
        keys = sorted(by_key, key=lambda k: -sum(by_key[k].values()))
        near = set()
        for i, k in enumerate(keys):
            for k2 in keys[:i]:   # compare with every more frequent value
                # Same word count, so "Strong Hire" vs "Strong No Hire" is not a misspelling;
                # digits ignored, so "Technical 1" vs "Technical 2" is not either.
                if (words[k] == words[k2] and re.sub(r"\d", "", k) != re.sub(r"\d", "", k2)
                        and difflib.SequenceMatcher(None, k, k2).ratio() >= NEAR_DUP_RATIO):
                    near.update(by_key[k])
        bad = [r for r, v in vals.items()
               if v != v.strip() or "  " in v or canonical[v] != v or v in near]
        affects = {"Source": (CH,), "Stage": (CH, ACC), "Status": (CH, ACC) if table != "Job Openings" else (D2,),
                   "Decline Reason": (ACC,), "Rejection Reason": (ACC,)}.get(fld, (D2,))
        if table == "Offers" and fld == "Status":
            affects = (ACC,)
        values = ", ".join(f"{v} ({n})" for v, n in distinct.most_common())
        self.add("H", f"Category variants in {fld}", table, fld, affects,
                 "Leading/trailing/double spaces, case or punctuation variants of the same value, or a value within "
                 f"{NEAR_DUP_RATIO} similarity of a more common one with the same word count. Values seen: {values}",
                 vals, bad)

    # ---- 6. Missing values -------------------------------------------------------

    def missing(self):
        for table, fld, affects in MISSING_FIELDS:
            recs = self.t[table]
            self.add("M", f"{fld} missing", table, fld, affects,
                     f"`{table}.{fld}` empty or absent (Airtable omits empty fields)", recs,
                     [r for r, f in recs.items() if empty(f.get(fld))])

    # ---- 7. Other --------------------------------------------------------------------

    def other(self):
        A, O, I, J, C, P = (self.t[k] for k in ("Applications", "Offers", "Interviews", "Job Openings",
                                                "Candidates", "People"))
        for table, fld in BUSINESS_CREATED.items():
            recs, ct = self.t[table], self.created.get(table, {})
            chk = [r for r, f in recs.items() if d(f.get(fld)) and d(ct.get(r))]
            bad = [r for r in chk if d(ct[r]) != d(recs[r][fld])]
            span = sorted({ct[r][:10] for r in chk}) or ["none"]
            self.add("O", "Airtable createdTime differs from the business creation date", table,
                     f"createdTime vs {fld}", (D2,),
                     f"createdTime date ≠ {fld}. createdTime dates in this table: {span[0]} to {span[-1]}. "
                     "If all records share one load date, createdTime cannot stand in for event time",
                     chk, bad, in_union=False)

        chk = [j for j, f in J.items() if isinstance(f.get("Req ID"), str) and d(f.get("Opened On"))]
        bad = [j for j in chk if not J[j]["Req ID"].startswith(f"REQ-{d(J[j]['Opened On']).year}-")]
        self.add("O", "Req ID year differs from Opened On year", "Job Openings", "Req ID vs Opened On", (D2,),
                 "The year embedded in Req ID (REQ-YYYY-nnn) is not the year of Opened On", chk, bad)
        chk = [j for j, f in J.items() if isinstance(f.get("Title"), str) and f["Title"].split()[0] in SENIORITY_PREFIX]
        bad = [j for j in chk if J[j].get("Level") != SENIORITY_PREFIX[J[j]["Title"].split()[0]]]
        self.add("O", "Title seniority prefix disagrees with Level", "Job Openings", "Title vs Level", (D2,),
                 "Title starts with Junior/Senior/Lead but Level is different", chk, bad)
        bands = defaultdict(Counter)
        for f in J.values():
            bands[f.get("Level")][(f.get("Salary Band Min"), f.get("Salary Band Max"))] += 1
        chk = [j for j, f in J.items() if f.get("Level")]
        bad = [j for j in chk if bands[J[j]["Level"]].most_common(1)[0][0]
               != (J[j].get("Salary Band Min"), J[j].get("Salary Band Max"))]
        self.add("O", "Salary band differs from the usual band for its Level", "Job Openings", "Level vs Salary Band",
                 (ACC,), "Band (Min, Max) differs from the most common band among openings at the same Level", chk, bad)
        chk = [j for j, f in J.items() if one(f, "Hiring Manager") in P and one(f, "Department")]
        bad = [j for j in chk if one(P[one(J[j], "Hiring Manager")], "Department") != one(J[j], "Department")]
        self.add("O", "Hiring manager belongs to a different department than the opening", "Job Openings",
                 "Hiring Manager.Department vs Department", (D2,),
                 "Hiring manager's People.Department ≠ opening's Department", chk, bad)

        chk = [c for c, f in C.items() if isinstance(f.get("Expected CTC"), (int, float))
               and isinstance(f.get("Current CTC"), (int, float))]
        bad = [c for c in chk if C[c]["Expected CTC"] < C[c]["Current CTC"]]
        self.add("O", "Expected CTC below Current CTC", "Candidates", "Expected CTC vs Current CTC", (ACC,),
                 "Expected CTC < Current CTC (plausibility, not necessarily wrong)", chk, bad)
        for table, flds, affects in (("Candidates", ("Expected CTC", "Current CTC"), (ACC,)), ("Offers", ("Base CTC",), (ACC,))):
            recs = self.t[table]
            chk = [r for r, f in recs.items() if any(isinstance(f.get(k), (int, float)) for k in flds)]
            bad = [r for r in chk if any(isinstance(recs[r].get(k), (int, float)) and recs[r][k] % 10000 for k in flds)]
            self.add("O", "CTC not a round figure", table, ", ".join(flds), affects,
                     "CTC not a multiple of 10,000 while almost all others are (possible entry or conversion error)",
                     chk, bad)
        chk, bad = [], []
        for o, f in O.items():
            c = C.get(one(A.get(one(f, "Application"), {}), "Candidate"))
            if c and isinstance(f.get("Base CTC"), (int, float)) and isinstance(c.get("Current CTC"), (int, float)):
                chk.append(o)
                if f["Base CTC"] < c["Current CTC"]:
                    bad.append(o)
        self.add("O", "Offer Base CTC below the candidate's Current CTC", "Offers", "Base CTC vs Candidates.Current CTC",
                 (ACC,), "Base CTC < candidate's Current CTC (plausibility)", chk, bad)
        chk, bad = [], []
        for o, f in O.items():
            c = C.get(one(A.get(one(f, "Application"), {}), "Candidate"))
            dec, start = d(f.get("Decision On")), d(f.get("Proposed Start Date"))
            if f.get("Status") == "Accepted" and c and dec and start and isinstance(c.get("Notice Period Days"), (int, float)):
                chk.append(o)
                if (start - dec).days < c["Notice Period Days"]:
                    bad.append(o)
        self.add("O", "Accepted offer starts sooner than the candidate's notice period allows", "Offers",
                 "Proposed Start Date − Decision On vs Notice Period Days", (ACC,),
                 "Status=Accepted and Proposed Start Date − Decision On < Candidates.Notice Period Days", chk, bad)

        chk = [a for a, f in A.items() if one(f, "Recruiter") and one(J.get(one(f, "Opening"), {}), "Recruiter")]
        bad = [a for a in chk if one(A[a], "Recruiter") != one(J[one(A[a], "Opening")], "Recruiter")]
        self.add("O", "Application recruiter differs from the opening's recruiter", "Applications",
                 "Recruiter vs Job Openings.Recruiter", (D2,), "Applications.Recruiter ≠ linked opening's Recruiter",
                 chk, bad)
        chk = [c for c, f in C.items() if any(A.get(a, {}).get("Stage") == "Hired" for a in links(f, "Applications"))]
        bad = [c for c in chk if any(A.get(a, {}).get("Status") == "Active" for a in links(C[c], "Applications"))]
        self.add("O", "Hired candidate still has another active application", "Candidates", "Applications.Stage/Status",
                 (CH,), "Candidate has a Stage=Hired application and another with Status=Active", chk, bad)
        chk = [a for a, f in A.items() if links(f, "Referred By") and links(f, "Interviews")]
        bad = [a for a in chk if set(links(A[a], "Referred By")) & {one(I.get(i, {}), "Interviewer") for i in links(A[a], "Interviews")}]
        self.add("O", "Referrer also interviewed the candidate", "Applications", "Referred By vs Interviews.Interviewer",
                 (D2,), "A person in Referred By is the Interviewer on one of the application's interviews", chk, bad)
        order = {"Screen": 0, "Technical 1": 1, "Technical 2": 2, "Hiring Manager": 3, "Bar Raiser": 3}
        chk, bad = [], []
        for a, f in A.items():
            ivs = [I[i] for i in links(f, "Interviews") if i in I and I[i].get("Round") in order and d(I[i].get("Scheduled On"))]
            if len(ivs) < 2:
                continue
            chk.append(a)
            rounds = {iv["Round"] for iv in ivs}
            seq = [iv["Round"] for iv in sorted(ivs, key=lambda iv: iv["Scheduled On"])]
            if ("Technical 2" in rounds and "Technical 1" not in rounds) or any(
                    order[x] > order[y] for x, y in zip(seq, seq[1:])):
                bad.append(a)
        self.add("O", "Interview rounds out of sequence", "Applications", "Interviews.Round order", (D2,),
                 "Among applications with ≥2 interviews: a later round scheduled before an earlier one "
                 "(Screen → Technical 1 → Technical 2 → Hiring Manager/Bar Raiser), or Technical 2 without Technical 1",
                 chk, bad)
        for table, fld in (("Candidates", "Notes"), ("Interviews", "Feedback")):
            recs = self.t[table]
            vals = {r: norm(f[fld]) for r, f in recs.items() if isinstance(f.get(fld), str) and f[fld].strip()}
            n = Counter(vals.values())
            self.add("O", f"{fld}: identical free text repeated across many records", table, fld,
                     (CH,) if table == "Candidates" else (D2,),
                     f"Free-text value shared verbatim by ≥ {TEMPLATE_MIN_RECORDS} records "
                     f"({len(n)} distinct texts across {len(vals)} filled records): text is templated, "
                     "so it carries little record-specific information", vals,
                     [r for r, v in vals.items() if n[v] >= TEMPLATE_MIN_RECORDS], in_union=False)

    def run(self):
        self.uniqueness()
        self.link_integrity()
        self.cross_field()
        self.dates()
        self.hygiene()
        self.missing()
        self.other()
        return self.checks


# ---- output --------------------------------------------------------------------------

def pct(c):
    return "n/a" if c.pct is None else f"{c.pct:.1f}%"


def cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_csv(checks):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["check_id", "category", "check", "table", "field", "records_checked", "records_failing",
                "pct_failing", "example_record_ids", "affects", "rule"])
    for c in checks:
        w.writerow([c.id, CATEGORIES[c.category], c.name, c.table, c.field, len(c.checked), len(c.failing),
                    "" if c.pct is None else f"{c.pct:.1f}", " ".join(c.examples), "; ".join(c.affects), c.rule])
    return buf.getvalue()


def to_md(checks, manifest, as_of, tables):
    L = ["# D4 data-quality audit", "",
         f"Generated by `python3 -m pipeline.audit` from the cache in `data/raw/` "
         f"(fetched {manifest.get('fetched_at')}; base `{manifest.get('base_id')}`). "
         f"Dates are judged against the fetch date, {as_of}. Re-running on the same cache gives identical output.", "",
         "Descriptive only: this counts problems and locates them. It does not interpret them for the VP's claims.", "",
         "## How to read this", "",
         "- **Checked** = records the rule applies to (e.g. only Pending offers for a Pending-offer rule). "
         "**Failing** = records in that set that break the rule. **%** = failing / checked.",
         "- **Examples** are Airtable record IDs from the table named in *Table*, first five in sort order. "
         "No names, emails or phones appear in this file.",
         "- **Affects** says which deliverable a failure could move: `D1 channel` (source-of-hire claim), "
         "`D1/D3 acceptance` (offer acceptance claim and metric spec), `D2` (roadmap sizing).",
         "- Duplicate checks count every record in a duplicate group, not just the extras.",
         "- Checks with 0 failing are listed too: they are evidence of what was looked for.",
         "", "## Limits of this audit", "",
         "- The schema metadata API returned 403, so field types are inferred from values. Fields that are empty on "
         "every record are invisible (Airtable omits empty fields), and declared-but-unused select options cannot be seen.",
         "- `Findings` has 0 records and no visible fields; nothing to audit there.",
         "- Plausibility thresholds (e.g. offers outstanding > "
         f"{DECISION_GAP_DAYS} days) are stated in each rule; they flag records to look at, not proven errors.",
         "", "## Inputs", "", "| Table | Records |", "|---|---:|"]
    L += [f"| {t} | {len(tables.get(t, {}))} |" for t in manifest.get("tables", tables)]

    L += ["", "## Summary by category", "",
          "| Category | Checks | Checks with failures | Failing records (sum over checks) |", "|---|---:|---:|---:|"]
    for k, name in CATEGORIES.items():
        cs = [c for c in checks if c.category == k]
        L.append(f"| {k} · {name} | {len(cs)} | {sum(1 for c in cs if c.failing)} | {sum(len(c.failing) for c in cs)} |")

    L += ["", "## Records failing at least one check, per table", "",
          "Union of failing records across all record-level checks on that table. Excludes the two field-level "
          "checks (createdTime vs business date, templated free text), which flag almost every record by design.", "",
          "| Table | Records | Failing ≥1 check | % |", "|---|---:|---:|---:|"]
    for t in ("Applications", "Candidates", "Offers", "Interviews", "Job Openings", "People", "Departments"):
        n = len(tables.get(t, {}))
        u = set().union(*[set(c.failing) for c in checks if c.table == t and c.in_union]) if checks else set()
        L.append(f"| {t} | {n} | {len(u)} | {100 * len(u) / n:.1f}% |" if n else f"| {t} | 0 | 0 | n/a |")

    head = ["| ID | Check | Table.field | Checked | Failing | % | Example record IDs | Affects |",
            "|---|---|---|---:|---:|---:|---|---|"]
    row = lambda c: (f"| {c.id} | {cell(c.name)} | {cell(c.table)}.{cell(c.field)} | {len(c.checked)} | "
                     f"{len(c.failing)} | {pct(c)} | {' '.join(c.examples)} | {', '.join(c.affects)} |")
    L += ["", "## Checks with failures", ""] + head
    L += [row(c) for c in checks if c.failing]
    L += ["", "## Checks with no failures", ""] + head
    L += [row(c) for c in checks if not c.failing]
    L += ["", "## Check definitions", ""]
    L += [f"- **{c.id}** {cell(c.name)}: {c.rule}" for c in checks]
    return "\n".join(L) + "\n"


def pii_strings(records):
    out = set()
    for table, flds in (("Candidates", ("Full Name", "Email", "Phone")), ("People", ("Full Name", "Work Email"))):
        for r in records.get(table, []):
            for k in flds:
                v = r.get("fields", {}).get(k)
                if isinstance(v, str) and len(v.strip()) >= 5:
                    out.add(v.strip())
    return out


def assert_no_pii(text, pii):
    leaked = [s for s in pii if s in text]
    if leaked or re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        raise RuntimeError(f"Refusing to write output: {len(leaked)} personal value(s) or an email address would leak")


def load():
    manifest = json.loads((RAW / "_manifest.json").read_text())
    records = {t: json.loads((RAW / info["file"]).read_text())["records"] for t, info in manifest["tables"].items()}
    return manifest, records


def main():
    manifest, records = load()
    as_of = date.fromisoformat(manifest["fetched_at"][:10])
    audit = Audit(records, as_of)
    checks = audit.run()
    md, csv_text = to_md(checks, manifest, as_of, audit.t), to_csv(checks)
    pii = pii_strings(records)
    assert_no_pii(md, pii)
    assert_no_pii(csv_text, pii)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data_quality.md").write_text(md)
    (OUT / "data_quality.csv").write_text(csv_text)
    failing = [c for c in checks if c.failing]
    print(f"{len(checks)} checks, {len(failing)} with failures -> {OUT / 'data_quality.md'}, data_quality.csv")


if __name__ == "__main__":
    main()
