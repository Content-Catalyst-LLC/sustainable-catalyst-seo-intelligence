import base64
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import seo_baseline  # noqa: E402
import seo_gsc  # noqa: E402
import seo_recommendations  # noqa: E402
import seo_store  # noqa: E402

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:  # the Studio server's identity check needs the optional dependency
    HAVE_CRYPTO = False

FIXTURE = Path(__file__).parent / "fixtures" / "sc_seo_01_mini"
ORIGIN = "https://sustainablecatalyst.com"


def baseline(tmp):
    out = Path(tmp) / "out"
    argv = ["--evidence-dir", str(FIXTURE), "--release-dir", str(FIXTURE / "release"), "--tool-dir", str(FIXTURE / "tools"),
            "--issue-groups", str(FIXTURE / "issue-groups.csv"),
            "--existing-redirects", str(FIXTURE / "release/plans/existing-redirects.txt"),
            "--output-dir", str(out), "--property", ORIGIN + "/", "--imported-at", "2026-11-24T14:00:00Z"]
    with redirect_stdout(io.StringIO()):
        seo_baseline.main(argv)
    return (json.loads((out / "seo-baseline-history.json").read_text()),
            json.loads((out / "seo-baseline-receipt.json").read_text()))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history, self.receipt = baseline(self.tmp.name)
        self.db = seo_store.connect(Path(self.tmp.name) / "s.sqlite")
        self.run_id = seo_store.import_history(self.db, self.history, "baseline", "baseline", self.receipt)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def count(self, sql, *args):
        return self.db.execute(sql, args).fetchone()[0]

    def test_every_observation_and_finding_is_stored(self):
        obs = sum(len(p["observations"]) for p in self.history["pages"])
        fin = sum(len(p["findings"]) for p in self.history["pages"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM observation"), obs)
        self.assertEqual(self.count("SELECT COUNT(*) FROM finding"), fin)
        self.assertEqual(self.count("SELECT COUNT(*) FROM finding WHERE evidence_class='prior'"), 4)

    def test_append_only(self):
        for sql in ("UPDATE finding SET priority='P0'", "DELETE FROM observation", "DELETE FROM run"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.db.execute(sql)

    def test_same_history_cannot_be_stored_twice(self):
        with self.assertRaises(ValueError):
            seo_store.import_history(self.db, self.history, "again", "baseline", self.receipt)

    def test_page_summary_matches_measured_findings(self):
        canon = self.count("SELECT COUNT(*) FROM finding WHERE action='Review declared canonical'")
        self.assertEqual(self.count("SELECT COUNT(*) FROM page WHERE canonical_self=0"), canon)
        self.assertEqual(self.count("SELECT noindex FROM page WHERE url=?", ORIGIN + "/noindex/"), 1)
        self.assertEqual(self.count("SELECT broken_links_out FROM page WHERE url=?", ORIGIN + "/"), 2)

    def test_compare_treats_absence_as_unknown(self):
        later = json.loads(json.dumps(self.history))
        later["pages"] = [p for p in later["pages"] if p["url"] != ORIGIN + "/a/"]
        for p in later["pages"]:
            for o in p["observations"]:
                if p["url"] == ORIGIN + "/old-slug/" and o["facts"].get("http_status") == 404:
                    o["facts"]["http_status"] = 301
        run2 = seo_store.import_history(self.db, later, "verification", "verification")
        diff = seo_store.compare(self.db, self.run_id, run2)
        self.assertEqual(diff["status_changed_count"], 1)
        self.assertEqual(diff["only_in_before"], 1)  # /a/ unobserved later: unknown, not "fixed"

    def test_recommendations_are_inferred_and_never_promote_unverified(self):
        recs = seo_recommendations.derive(self.history)
        kinds = {r["kind"] for r in recs}
        self.assertTrue({"redirect", "redirect_decision", "headings", "kb_governance"} <= kinds)
        self.assertTrue(all(r["evidence_class"] == "inferred" and r["approval"] == "required" for r in recs))
        self.assertFalse(any(r["kind"] == "kb_sitemap_candidate" for r in recs))  # only an unverified KB article here
        redirect = next(r for r in recs if r["kind"] == "redirect")
        self.assertIn("validated-not-deployed", redirect["summary"])
        seo_store.add_recommendations(self.db, self.run_id, recs)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO recommendation VALUES ('x','y','k','s','s','measured','{}','required','p')")


class FakeGoogle:
    def __init__(self, scope=seo_gsc.SCOPE):
        self.calls, self.scope = [], scope

    def __call__(self, url, body, headers):
        self.calls.append((url, headers.get("Authorization", "")))
        if url == seo_gsc.TOKEN_URL:
            return {"access_token": "test-token", "expires_in": 3600, "scope": self.scope}
        if url == seo_gsc.URL_INSPECTION:
            return {"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed",
                                                               "googleCanonical": json.loads(body)["inspectionUrl"]}}}
        return {"rows": [{"keys": [ORIGIN + "/a/"], "clicks": 1, "impressions": 400, "ctr": 0.0025, "position": 9.1}],
                "responseAggregationType": "byPage"}


SIGNER = {"client_email": "reader@project.iam.gserviceaccount.com", "sign": lambda claims: "header.claims.sig"}


class SearchConsoleConnector(unittest.TestCase):
    def test_refuses_write_scope_other_property_and_endpoints(self):
        with self.assertRaises(seo_gsc.Refused):
            seo_gsc.Client("sc-domain:sustainablecatalyst.com", scope="https://www.googleapis.com/auth/webmasters")
        client = seo_gsc.Client("sc-domain:sustainablecatalyst.com", transport=FakeGoogle(), signer=SIGNER)
        with self.assertRaises(seo_gsc.Refused):
            client.check_property("sc-domain:other.example")
        with self.assertRaises(seo_gsc.Refused):
            client._post("https://www.googleapis.com/webmasters/v3/sites/x/sitemaps/y", {})

    def test_refuses_broader_granted_scope(self):
        fake = FakeGoogle(scope=seo_gsc.SCOPE + " https://www.googleapis.com/auth/webmasters")
        client = seo_gsc.Client("sc-domain:sustainablecatalyst.com", transport=fake, signer=SIGNER)
        with self.assertRaises(seo_gsc.Refused):
            client.search_analytics("2026-10-01", "2026-10-31")

    def test_no_credential_means_no_request(self):
        fake = FakeGoogle()
        client = seo_gsc.Client("sc-domain:sustainablecatalyst.com", transport=fake)
        with self.assertRaises(seo_gsc.Refused):
            client.search_analytics("2026-10-01", "2026-10-31")
        self.assertEqual(fake.calls, [])

    def test_rejects_non_service_account_and_loose_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.json"
            path.write_text(json.dumps({"type": "authorized_user", "refresh_token": "x"}))
            path.chmod(0o600)
            with self.assertRaises(seo_gsc.Refused):
                seo_gsc.load_service_account(path)
            path.write_text(json.dumps({"type": "service_account", "client_email": "a", "private_key": "b"}))
            path.chmod(0o644)
            with self.assertRaises(seo_gsc.Refused):
                seo_gsc.load_service_account(path)

    def test_collect_produces_search_evidence_and_store_shows_it(self):
        fake = FakeGoogle()
        client = seo_gsc.Client("sc-domain:sustainablecatalyst.com", transport=fake, signer=SIGNER)
        history, receipt = seo_gsc.collect(client, ORIGIN + "/", "2026-10-01", "2026-10-31", [ORIGIN + "/a/"],
                                           "2026-11-24T00:00:00Z")
        self.assertEqual((receipt["performance_rows"], receipt["inspections"], receipt["requests"]), (1, 1, 3))
        self.assertTrue(all(a == "Bearer test-token" for u, a in fake.calls if u != seo_gsc.TOKEN_URL))
        recs = seo_recommendations.derive(history)
        self.assertEqual(recs, [])  # no crawl observation for the page: no snippet recommendation without page evidence
        with tempfile.TemporaryDirectory() as tmp:
            db = seo_store.connect(Path(tmp) / "s.sqlite")
            run = seo_store.import_history(db, history, "gsc", "connector")
            row = db.execute("SELECT search_impressions, google_coverage FROM page WHERE run_id=?", (run,)).fetchone()
            self.assertEqual(tuple(row), (400, "Submitted and indexed"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_performance").fetchone()[0], 1)
            db.close()


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional Studio server dependency)")
class AuthenticatedStudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        import dev_workspace_session as dev
        import seo_studio_server
        from seo_workspace_auth import Keyring
        cls.dev = dev
        cls.tmp = tempfile.TemporaryDirectory()
        history, receipt = baseline(cls.tmp.name)
        path = Path(cls.tmp.name) / "s.sqlite"
        db = seo_store.connect(path)
        cls.run_id = seo_store.import_history(db, history, "baseline", "baseline", receipt)
        seo_store.add_recommendations(db, cls.run_id, seo_recommendations.derive(history))
        db.close()
        cls.key, cls.key_id, keyring = dev.make_signer()
        cls.keyring = Keyring(keyring)
        handler = seo_studio_server.make_handler(seo_store.connect(path, readonly=True), cls.keyring,
                                                 frozenset({"workspace-user:owner"}), frozenset(),
                                                 "http://127.0.0.1:1/v1/seo-access/session", "http://127.0.0.1:1")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def get(self, path, auth=None):
        req = urllib.request.Request(self.base + path, headers={"Authorization": auth} if auth else {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read()), err.headers

    def session(self, **kw):
        return self.dev.issue(self.key, self.key_id, kw.pop("subject", "workspace-user:owner"), **kw)

    def test_shell_has_no_data_and_api_needs_session(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            html = resp.read().decode()
            self.assertIn("frame-ancestors 'none'", resp.headers["Content-Security-Policy"])
        self.assertNotIn("sustainablecatalyst.com/", html)
        status, body, headers = self.get("/api/runs")
        self.assertEqual((status, headers["WWW-Authenticate"]), (401, "SC-Workspace"))

    def test_refusals(self):
        now = datetime.now(timezone.utc)
        cases = {
            "not on allowlist": self.session(subject="workspace-user:someone"),
            "service credential": self.session(method="erw-account-session"),
            "expired": self.session(now=now - timedelta(seconds=400)),
            "too long": self.session(lifetime=3600),
        }
        tampered = self.session()
        doc = json.loads(base64.urlsafe_b64decode(tampered.split(" ")[1] + "=="))
        doc["subject"] = "workspace-user:other"
        cases["tampered"] = "SC-Workspace " + base64.urlsafe_b64encode(json.dumps(doc).encode()).decode()
        research = json.loads(json.dumps(doc))
        research["schema"] = "sc-workspace-research-session/1.0"
        cases["research session"] = "SC-Workspace " + base64.urlsafe_b64encode(json.dumps(research).encode()).decode()
        for name, auth in cases.items():
            self.assertEqual(self.get("/api/runs", auth)[0], 401, name)

    def test_validly_signed_research_session_from_the_same_key_is_refused(self):
        # A genuine Research session (signed by the same Workspace key, Research domain and audience) must not open
        # SEO Studio; nor may an SEO-shaped body signed under the Research domain.
        from seo_workspace_auth import canonical
        now = datetime.now(timezone.utc)

        def signed(domain, **over):
            body = {"schema": domain, "issuer": "sustainable-catalyst-workspace", "audience": "sc-research",
                    "subject": "workspace-user:owner", "authentication": "sc-identity-oidc-session",
                    "issuedAt": now.isoformat(), "expiresAt": (now + timedelta(seconds=120)).isoformat(), **over}
            sig = self.key.sign(body["schema"].encode() + b"\n" + canonical(body))
            doc = {**body, "signature": {"alg": "Ed25519", "keyId": self.key_id,
                                         "value": base64.urlsafe_b64encode(sig).rstrip(b"=").decode()}}
            return "SC-Workspace " + base64.urlsafe_b64encode(canonical(doc)).rstrip(b"=").decode()

        research = "sc-workspace-research-session/1.0"
        self.assertEqual(self.get("/api/runs", signed(research))[0], 401)
        self.assertEqual(self.get("/api/runs", signed(research, audience="sc-seo-intelligence"))[0], 401)
        self.assertEqual(self.get("/api/runs", signed("sc-workspace-seo-session/1.0"))[0], 401)  # wrong audience
        self.assertEqual(self.get("/api/runs", signed("sc-workspace-seo-session/1.0",
                                                      audience="sc-seo-intelligence"))[0], 200)  # control

    def test_dashboard_api(self):
        auth = self.session()
        status, body, _ = self.get("/api/runs", auth)
        self.assertEqual((status, body["subject"], len(body["runs"])), (200, "workspace-user:owner", 1))
        _, o, _ = self.get(f"/api/overview?run={self.run_id}", auth)
        self.assertEqual(o["broken_links"]["edges"], 4)
        self.assertEqual(o["broken_links"]["top_targets"][0], {"target": ORIGIN + "/gone/", "inlinks": 3})
        self.assertIn("No search-engine evidence", o["indexing"]["note"])
        self.assertEqual(o["knowledge_base"]["verification_states"], {"not_verified": 1})
        self.assertEqual(o["change_events"][0]["state"], "validated-not-deployed")
        _, pages, _ = self.get(f"/api/pages?run={self.run_id}&filter=multiple_h1", auth)
        self.assertEqual([r["url"] for r in pages["rows"]], [ORIGIN + "/b/"])
        _, detail, _ = self.get(f"/api/page?run={self.run_id}&url=" + urllib.request.quote(ORIGIN + "/b/"), auth)
        self.assertEqual({f["evidence_class"] for f in detail["findings"]}, {"measured", "prior"})
        _, recs, _ = self.get(f"/api/recommendations?run={self.run_id}&kind=redirect", auth)
        self.assertEqual(recs["total"], 1)
        self.assertEqual(self.get(f"/api/pages?run={self.run_id}&filter=drop", auth)[0], 400)
        self.assertEqual(self.get("/api/overview?run=nope", auth)[0], 400)


if __name__ == "__main__":
    unittest.main()
