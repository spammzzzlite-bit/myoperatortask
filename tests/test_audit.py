"""Unit test of the data-quality audit on a tiny synthetic base with planted defects.

    python3 -m unittest tests.test_audit -v

Synthetic data only. Checks that each planted defect is caught by the right
check, that clean records pass, and that the PII guard blocks names and emails.
"""
import unittest
from datetime import date

from pipeline import audit


def rec(i, **fields):
    return {"id": i, "createdTime": "2026-08-27T00:00:00.000Z", "fields": fields}


def make():
    people = [rec("recP1", **{"Full Name": "Test Recruiter", "Role": "Recruiter", "Joined On": "2020-01-01",
                              "Applications as Recruiter": ["recA1", "recA2", "recA3"], "Reqs as Recruiter": ["recJ1"]}),
              rec("recP2", **{"Full Name": "Test Manager", "Role": "Hiring Manager", "Joined On": "2020-01-01",
                              "Reqs as Hiring Manager": ["recJ1"]})]
    jobs = [rec("recJ1", **{"Req ID": "REQ-2026-001", "Opened On": "2026-01-01", "Status": "Filled", "Headcount": 1,
                            "Level": "Mid", "Salary Band Min": 100, "Salary Band Max": 200,
                            "Recruiter": ["recP1"], "Hiring Manager": ["recP2"],
                            "Applications": ["recA1", "recA2", "recA3"]})]
    cands = [rec("recC1", **{"Candidate ID": "CAND-00001", "Full Name": "Ann Example", "Phone": "+91 9000000001",
                             "Email": "ann1@example.com", "Source": "Job Board", "Created On": "2026-01-02",
                             "Applications": ["recA1", "recA2"]}),
             rec("recC2", **{"Candidate ID": "CAND-00002", "Full Name": "Ann Example", "Phone": "+91 9000000001",
                             "Email": "ann2@example.com", "Source": "Referral", "Created On": "2026-01-02",
                             "Applications": ["recA3"]})]
    apps = [rec("recA1", **{"Application ID": "APP-00001", "Candidate": ["recC1"], "Opening": ["recJ1"],
                            "Recruiter": ["recP1"], "Stage": "Hired", "Status": "Closed", "Applied On": "2026-01-05",
                            "Closed On": "2026-02-01", "Offers": ["recO1"], "Offered On": "2026-01-20"}),
            rec("recA2", **{"Application ID": "APP-00001", "Candidate": ["recC1"], "Opening": ["recJ1"],
                            "Recruiter": ["recP1"], "Stage": "Offer", "Status": "Closed", "Applied On": "2026-01-06",
                            "Closed On": "2026-01-04"}),
            rec("recA3", **{"Application ID": "APP-00003", "Candidate": ["recC2"], "Opening": ["recJ1"],
                            "Recruiter": ["recP1"], "Stage": "Applied", "Status": "Active", "Applied On": "2026-01-07"})]
    offers = [rec("recO1", **{"Offer ID": "OFF-00001", "Application": ["recA1"], "Status": "Pending",
                              "Offered On": "2026-01-20", "Decision On": "2026-01-25", "Base CTC": 500,
                              "Proposed Start Date": "2026-03-01"})]
    return {"People": people, "Job Openings": jobs, "Candidates": cands, "Applications": apps,
            "Offers": offers, "Interviews": [], "Departments": [], "Findings": []}


class AuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = make()
        cls.checks = {c.name: c for c in audit.Audit(cls.records, date(2026, 3, 1)).run()}

    def failing(self, name):
        return self.checks[name].failing

    def test_planted_defects_found(self):
        self.assertEqual(self.failing("Duplicate Application ID"), ["recA1", "recA2"])
        self.assertEqual(self.failing("Likely duplicate candidate: same name and phone, different email"),
                         ["recC1", "recC2"])
        self.assertEqual(self.failing("Stage disagrees with Status"), ["recA2"])
        self.assertEqual(self.failing("Closed On before Applied On"), ["recA2"])
        self.assertEqual(self.failing("Hired application without an Accepted offer"), ["recA1"])
        self.assertEqual(self.failing("Pending offer has a Decision On date"), ["recO1"])
        self.assertEqual(self.failing("Offer Base CTC outside the opening's salary band"), ["recO1"])
        self.assertEqual(self.failing("Offer-stage application with no offer record"), ["recA2"])
        self.assertEqual(self.failing("Referral-sourced candidate with no referrer on any application"), ["recC2"])
        self.assertEqual(self.failing("Same candidate applied to the same opening more than once"), ["recA1", "recA2"])

    def test_clean_records_pass(self):
        self.assertEqual(self.failing("Applications.Candidate → Candidates: target exists and links back"), [])
        self.assertEqual(self.checks["Filled opening whose hires ≠ Headcount"].checked, ["recJ1"])
        self.assertEqual(self.failing("Filled opening whose hires ≠ Headcount"), [])

    def test_checked_is_denominator(self):
        c = self.checks["Pending offer has a Decision On date"]
        self.assertEqual((len(c.checked), len(c.failing), round(c.pct)), (1, 1, 100))

    def test_outputs_have_no_pii(self):
        pii = audit.pii_strings(self.records)
        checks = list(self.checks.values())
        md = audit.to_md(checks, {"tables": {}}, date(2026, 3, 1), {})
        audit.assert_no_pii(md, pii)
        audit.assert_no_pii(audit.to_csv(checks), pii)
        with self.assertRaises(RuntimeError):
            audit.assert_no_pii("row for Ann Example", pii)
        with self.assertRaises(RuntimeError):
            audit.assert_no_pii("contact someone@corp.io", set())


if __name__ == "__main__":
    unittest.main()
