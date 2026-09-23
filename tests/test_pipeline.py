"""End-to-end test of ingest + profile against a local mock of the Airtable API.

    python3 -m unittest tests.test_pipeline -v

The mock uses synthetic data only. It checks that we: paginate to exhaustion
without duplicates, stay under 5 req/s, recover from a 429 and an expired
offset, and never send or write the token anywhere but the auth header.
"""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pipeline.airtable as airtable
import pipeline.ingest as ingest
import pipeline.profile as profile

TOKEN = "patFAKEFAKEFAKE12.0123456789abcdef0123456789abcdef"
BASE = "appTEST0000000000"


def rid(prefix, i):
    return f"rec{prefix}{i:011d}"[:17]


def make_data():
    cands = [{"id": rid("CAN", i), "createdTime": "2026-01-01T00:00:00.000Z",
              "fields": {"Name": f"Cand {i}", "Source": ["Job Board", "Referral", "job board"][i % 3]}}
             for i in range(230)]
    apps = [{"id": rid("APP", i), "createdTime": "2026-01-02T00:00:00.000Z",
             "fields": {"App ID": f"A-{i}", "Candidate": [cands[i % 230]["id"]],
                        "Stage": ["Applied", "Hired", "Rejected"][i % 3],
                        **({"Applied Date": "2026-02-01"} if i % 4 else {})}}
            for i in range(250)]
    apps[0]["fields"]["Candidate"] = ["recDANGLING0000000"[:17]]
    offers = [{"id": rid("OFF", i), "createdTime": "2026-01-03T00:00:00.000Z",
               "fields": {"Application": [apps[i]["id"]], "App Ref": f"A-{i}",
                          "Status": ["Accepted", "Declined"][i % 2], "Salary": 50000 + i,
                          **({"Score": {"specialValue": "NaN"}} if i == 3 else {})}}
              for i in range(40)]
    return {"Candidates": cands, "Applications": apps, "Offers": offers}


SCHEMA = [{"id": "tblC", "name": "Candidates", "primaryFieldId": "f1",
           "fields": [{"id": "f1", "name": "Name", "type": "singleLineText"},
                      {"id": "f2", "name": "Source", "type": "singleSelect",
                       "options": {"choices": [{"name": "Job Board"}, {"name": "Referral"}, {"name": "Agency"}]}},
                      {"id": "f3", "name": "Never Used", "type": "singleLineText"}]},
          {"id": "tblA", "name": "Applications", "primaryFieldId": "f4",
           "fields": [{"id": "f4", "name": "App ID", "type": "singleLineText"}]},
          {"id": "tblO", "name": "Offers", "primaryFieldId": "f5",
           "fields": [{"id": "f5", "name": "Application", "type": "multipleRecordLinks"}]}]


class Mock(BaseHTTPRequestHandler):
    data, stamps, auth_ok = {}, [], True
    fail_429_at, expire_offset_once = {5}, True

    def log_message(self, *a):
        pass

    def send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        cls = type(self)
        cls.stamps.append(time.monotonic())
        cls.auth_ok &= self.headers.get("Authorization") == f"Bearer {TOKEN}"
        if len(cls.stamps) in cls.fail_429_at:
            return self.send(429, {"errors": [{"error": "RATE_LIMIT_REACHED"}]})
        u = urlparse(self.path)
        if u.path == f"/v0/meta/bases/{BASE}/tables":
            return self.send(200, {"tables": SCHEMA})
        table = unquote(u.path.split("/")[-1])
        q = parse_qs(u.query)
        size = int(q.get("pageSize", [100])[0])
        start = int(q.get("offset", ["0"])[0].split("/")[-1])
        if table == "Applications" and start == 200 and cls.expire_offset_once:
            cls.expire_offset_once = False
            return self.send(422, {"error": {"type": "LIST_RECORDS_ITERATOR_NOT_AVAILABLE"}})
        recs = cls.data[table][start:start + size]
        body = {"records": recs}
        if start + size < len(cls.data[table]):
            body["offset"] = f"itr/{start + size}"
        self.send(200, body)


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Mock.data = make_data()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.patches = [(airtable, "API_URL", f"http://127.0.0.1:{cls.server.server_port}"),
                       (airtable, "LOCKOUT_S", 0.2),
                       (ingest, "RAW", cls.tmp / "raw"), (ingest, "BRIEF_TABLES", list(Mock.data)),
                       (profile, "RAW", cls.tmp / "raw"), (profile, "OUT", cls.tmp / "profile")]
        cls.saved = [(m, k, getattr(m, k)) for m, k, _ in cls.patches]
        for m, k, v in cls.patches:
            setattr(m, k, v)
        orig_init = airtable.Airtable.__init__
        airtable.Airtable.__init__ = lambda self, **kw: orig_init(self, token=TOKEN, base_id=BASE, log=lambda *_: None)
        cls.saved.append((airtable.Airtable, "__init__", orig_init))
        cls.manifest = ingest.main(refresh=True)
        profile.main()
        cls.prof = json.loads((cls.tmp / "profile" / "profile.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for m, k, v in cls.saved:
            setattr(m, k, v)

    def test_all_records_downloaded_once(self):
        for t, recs in Mock.data.items():
            cached = json.loads((self.tmp / "raw" / f"{t}.json").read_text())["records"]
            self.assertEqual([r["id"] for r in cached], [r["id"] for r in recs], t)
        self.assertEqual(self.manifest["tables"]["Applications"]["pages"], 3)

    def test_rate_limit_respected(self):
        s = Mock.stamps
        worst = max(sum(1 for t in s if a <= t < a + 1.0) for a in s)
        self.assertLessEqual(worst, 5, f"{worst} requests in one second")

    def test_token_only_in_auth_header(self):
        self.assertTrue(Mock.auth_ok)
        for f in list((self.tmp / "raw").iterdir()) + list((self.tmp / "profile").iterdir()):
            self.assertNotIn(TOKEN, f.read_text(), f.name)
        self.assertNotIn(TOKEN, repr(airtable.Airtable()))

    def test_profile_contents(self):
        t = self.prof["tables"]
        apps = {f["field"]: f for f in t["Applications"]["fields"]}
        self.assertEqual(apps["Applied Date"]["missing_rate"], round(63 / 250, 4))
        self.assertEqual(apps["Candidate"]["links_to"], {"Candidates": 249, "<unresolved>": 1})
        self.assertEqual(apps["Stage"]["value_counts"]["Hired"], 83)
        cands = {f["field"]: f for f in t["Candidates"]["fields"]}
        self.assertIn("job board", cands["Source"]["value_counts"])       # raw casing preserved
        self.assertEqual(t["Candidates"]["fields_declared_but_never_filled"], ["Never Used"])
        offers = {f["field"]: f for f in t["Offers"]["fields"]}
        self.assertTrue(any("special values" in fl for fl in offers["Score"]["flags"]))
        self.assertIn({"from": "Offers.App Ref", "to": "Applications.App ID", "match_rate": 1.0},
                      self.prof["text_key_links"])


if __name__ == "__main__":
    unittest.main()
