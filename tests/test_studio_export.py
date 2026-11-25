"""SC-SEO-STUDIO-EXPORT-01: Excel review workbook. Entirely synthetic; standard library only (openpyxl, when
installed in a developer environment, is used for an extra independent read of the workbook)."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import seo_gsc  # noqa: E402
import seo_recommendations  # noqa: E402
import seo_store  # noqa: E402
import seo_studio_export as ex  # noqa: E402
import seo_xlsx  # noqa: E402
from test_studio import ORIGIN, SIGNER, FakeGoogle, baseline  # noqa: E402

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False
try:
    import openpyxl
except ImportError:
    openpyxl = None

NS = {"m": seo_xlsx.NS, "r": seo_xlsx.NS_R, "p": seo_xlsx.NS_PKG}
FILTERS = None  # resolved lazily from the server module (it imports only the standard library at module level)
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def filters():
    global FILTERS
    if FILTERS is None:
        import seo_studio_server
        FILTERS = seo_studio_server.FILTERS
    return FILTERS


class Book:
    """Independent, standard-library reader of the written package."""

    def __init__(self, data):
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        wb = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))}
        self.order = [s.get("name") for s in wb.find("m:sheets", NS)]
        self.paths = {s.get("name"): "xl/" + rels[s.get(f"{{{seo_xlsx.NS_R}}}id")] for s in wb.find("m:sheets", NS)}
        self.defined = [d.text for d in wb.iter(f"{{{seo_xlsx.NS}}}definedName")]

    def xml(self, name):
        return ET.fromstring(self.zip.read(self.paths[name]))

    def rows(self, name):
        out = []
        for row in self.xml(name).iter(f"{{{seo_xlsx.NS}}}row"):
            cells = {}
            for c in row:
                col = "".join(ch for ch in c.get("r") if ch.isalpha())
                t = c.find("m:is/m:t", NS)
                v = c.find("m:v", NS)
                cells[col] = t.text if t is not None else (int(v.text) if v is not None else None)
            width = max((self._index(k) for k in cells), default=-1) + 1
            out.append([cells.get(seo_xlsx.col_letter(i)) for i in range(width)])
        return out

    @staticmethod
    def _index(col):
        n = 0
        for ch in col:
            n = n * 26 + ord(ch) - 64
        return n - 1

    def table(self, name):
        rows = self.rows(name)
        header = rows[1]
        return [dict(zip(header, r + [None] * (len(header) - len(r)))) for r in rows[2:]]

    def overview(self):
        return {r[0]: (r + [None])[1] for r in self.rows("Overview")[2:]}


def store(tmp, gsc=False):
    path = Path(tmp) / "s.sqlite"
    db = seo_store.connect(path)
    history, receipt = baseline(tmp)
    run = seo_store.import_history(db, history, "baseline", "baseline", receipt)
    seo_store.add_recommendations(db, run, seo_recommendations.derive(history))
    gsc_run = None
    if gsc:
        client = seo_gsc.Client("sc-domain:sustainablecatalyst.com", transport=FakeGoogle(), signer=SIGNER)
        h, _ = seo_gsc.collect(client, ORIGIN + "/", "2026-10-01", "2026-10-31", [ORIGIN + "/a/"],
                               "2026-11-24T00:00:00Z")
        gsc_run = seo_store.import_history(db, h, "gsc", "connector")
    db.close()
    return path, run, gsc_run


def export(path, run, **kw):
    kw.setdefault("filters", filters())
    kw.setdefault("now", NOW)
    db = seo_store.connect(path, readonly=True)
    try:
        return ex.export_workbook(db, run, **kw)
    finally:
        db.close()


def synthetic_run(path, run_id, pages=(), recs=(), findings=(), observations=()):
    """Insert a run directly (the store is append-only; inserts are allowed)."""
    db = sqlite3.connect(path)
    with db:
        db.execute("INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?)", (
            run_id, "synthetic " + run_id, "baseline", "2026-11-24T14:00:00Z",
            json.dumps({"start": None, "end": "2026-11-23T22:59Z", "precision": "run"}),
            hashlib.sha256(run_id.encode()).hexdigest(), ORIGIN + "/", None, "{}"))
        db.execute("INSERT INTO artifact VALUES (?,?,?,?,?)", (run_id, "http_crawl", "a" * 64, "crawl.tsv", "{}"))
        for url, extra in pages:
            row = {"http_status": 200, "measured_findings": 0, "prior_findings": 0, "broken_links_out": 0, **extra}
            cols = ["run_id", "url", *row]
            db.execute(f"INSERT INTO page ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", (run_id, url, *row.values()))
        for oid, url, kind, facts in observations:
            db.execute("INSERT INTO observation VALUES (?,?,?,?,?,?,?,?)",
                       (run_id, oid, url, kind, "a" * 64, 1, None, json.dumps(facts)))
        for url, prio, action, oid, cls in findings:
            db.execute("INSERT INTO finding (run_id, url, priority, action, observation_id, evidence_class) "
                       "VALUES (?,?,?,?,?,?)", (run_id, url, prio, action, oid, cls))
        for rid, kind, subject, summary in recs:
            db.execute("INSERT INTO recommendation VALUES (?,?,?,?,?,?,?,?,?)",
                       (run_id, rid, kind, subject, summary, "inferred", "{}", "required", "proposed"))
    db.close()


class WriterTest(unittest.TestCase):
    def write(self, rows, cols=None):
        cols = cols or [seo_xlsx.Column("Text", 20), seo_xlsx.Column("URL", 30, "url"),
                        seo_xlsx.Column("Decision", 12, review=True, choices=("Approve", "Reject"))]
        buf = io.BytesIO()
        seo_xlsx.write(buf, [seo_xlsx.Sheet("Data", cols, iter(rows), "notice")], "t", "2026-11-25T00:00:00Z")
        return buf.getvalue()

    def test_no_formulas_and_formula_text_is_quoted(self):
        hostile = ["=HYPERLINK(\"http://x.invalid\",\"y\")", "+1+1", "-2", "@SUM(A1)", "\t=1", "  =1", "＝1",
                   "=cmd|' /C calc'!A0"]
        data = self.write([[h, "javascript:alert(1)", h] for h in hostile])
        book = Book(data)
        for part in book.zip.namelist():
            self.assertNotIn(b"<f>", book.zip.read(part), part)
            self.assertNotIn(b"<f ", book.zip.read(part), part)
        sheet = book.xml("Data")
        styles = ET.fromstring(book.zip.read("xl/styles.xml")).find("m:cellXfs", NS)
        for c in sheet.iter(f"{{{seo_xlsx.NS}}}c"):
            if c.get("r")[0] in "AC" and int(c.get("r")[1:]) > 2:
                self.assertEqual(styles[int(c.get("s"))].get("quotePrefix"), "1", c.get("r"))
        self.assertEqual([r[0] for r in book.rows("Data")[2:]], hostile)  # text preserved exactly
        self.assertNotIn("worksheets/_rels/sheet1.xml.rels", book.zip.namelist())  # javascript: never linked

    def test_unicode_control_characters_and_cell_limit(self):
        text = "Ünïcødé — 中文 — עברית — 🌍 — é"
        data = self.write([[text + "\x00\x07\x1b", "https://sustainablecatalyst.com/ü/?q=a&b=c", ""], ["quote", "https://sustainablecatalyst.com/a\"b/", ""],
                           ["x" * 40000, "https://sustainablecatalyst.com/a/", "Approve"]])
        book = Book(data)
        rows = book.rows("Data")
        self.assertEqual(rows[2][0], text)
        self.assertEqual(len(rows[4][0]), seo_xlsx.MAX_CELL)
        self.assertTrue(rows[4][0].endswith(seo_xlsx.TRUNCATED))
        rels = ET.fromstring(book.zip.read("xl/worksheets/_rels/sheet1.xml.rels"))
        self.assertEqual([r.get("Target") for r in rels], ["https://sustainablecatalyst.com/ü/?q=a&b=c",
                                                         "https://sustainablecatalyst.com/a/"])
        self.assertTrue(all(r.get("TargetMode") == "External" for r in rels))

    def test_sheet_names_and_layout(self):
        with self.assertRaises(ValueError):
            seo_xlsx.Sheet("x" * 32, [], [])
        with self.assertRaises(ValueError):
            seo_xlsx.Sheet("a/b", [], [])
        book = Book(self.write([["a", "", ""]]))
        sheet = book.xml("Data")
        pane = sheet.find("m:sheetViews/m:sheetView/m:pane", NS)
        self.assertEqual((pane.get("state"), pane.get("topLeftCell")), ("frozen", "A3"))
        self.assertEqual(sheet.find("m:autoFilter", NS).get("ref"), "A2:C3")
        self.assertEqual(len(sheet.find("m:cols", NS)), 3)
        dv = sheet.find("m:dataValidations/m:dataValidation", NS)
        self.assertEqual((dv.get("sqref"), dv.find("m:formula1", NS).text), ("C3:C3", '"Approve,Reject"'))
        self.assertEqual(book.defined, ["'Data'!$A$2:$C$3"])


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path, cls.run_id, _ = store(cls.tmp.name)
        cls.data, cls.name, cls.counts = export(cls.path, cls.run_id, subject="workspace-user:owner")
        cls.book = Book(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def db(self):
        return seo_store.connect(self.path, readonly=True)

    def test_structure(self):
        self.assertEqual(self.book.order, ["Overview", "Recommendations", "URL Inventory", "Indexing Issues",
                                           "Broken Links", "Metadata & H1", "Other Findings", "Evidence & Sources"])
        ov = self.book.overview()
        self.assertEqual(ov["Omitted sheet: Category Archives"], "no rows in this run/scope")
        self.assertEqual(ov["Export schema"], ex.EXPORT_SCHEMA)
        self.assertEqual(ov["Exported by (Workspace subject)"], "workspace-user:owner")
        self.assertTrue(self.name.endswith("-run-20261125T120000Z.xlsx"))
        for name in self.book.order:
            rows = self.book.rows(name)
            self.assertTrue(rows[0][0].startswith("HISTORICAL SNAPSHOT — not current site state."), name)
            if name != "Overview":
                header = rows[1]
                self.assertIn("Run ID", header)
                if name != "Evidence & Sources":
                    self.assertEqual(header[-6:], ["Row fingerprint", "Review decision", "Review status",
                                                   "Reviewer notes", "Reviewed by", "Reviewed on (YYYY-MM-DD)"])
        rels = ET.fromstring(self.book.zip.read("xl/_rels/workbook.xml.rels"))
        self.assertEqual(sum(r.get("Type").endswith("/worksheet") for r in rels), len(self.book.order))
        self.assertEqual(len(self.book.defined), len(self.book.order))

    def test_row_counts_match_store(self):
        db = self.db()
        one = lambda sql: db.execute(sql, (self.run_id,)).fetchone()[0]  # noqa: E731
        self.assertEqual(len(self.book.table("Recommendations")), one("SELECT COUNT(*) FROM recommendation WHERE run_id=?"))
        self.assertEqual(len(self.book.table("URL Inventory")), one("SELECT COUNT(*) FROM page WHERE run_id=?"))
        findings = sum(len(self.book.table(s)) for s in ("Indexing Issues", "Broken Links", "Metadata & H1",
                                                         "Other Findings"))
        self.assertEqual(findings, one("SELECT COUNT(*) FROM finding WHERE run_id=?"))
        db.close()
        for name, n in self.counts.items():
            self.assertEqual(len(self.book.table(name)), n)
            self.assertEqual(self.book.overview()[f"Rows: {name}"], str(n))

    def test_stable_ids_and_fingerprints(self):
        again = Book(export(self.path, self.run_id, subject="workspace-user:owner")[0])
        for name in ("Recommendations", "URL Inventory", "Broken Links", "Metadata & H1"):
            a = [(r["Record ID"], r["Row fingerprint"]) for r in self.book.table(name)]
            self.assertEqual(a, [(r["Record ID"], r["Row fingerprint"]) for r in again.table(name)])
            self.assertEqual(len({i for i, _ in a}), len(a), name)  # unique within the sheet
        db = self.db()
        ids = {r[0] for r in db.execute("SELECT recommendation_id FROM recommendation WHERE run_id=?", (self.run_id,))}
        db.close()
        self.assertEqual({r["Record ID"] for r in self.book.table("Recommendations")}, ids)

    def test_fingerprint_recomputes_from_returned_cells(self):
        # What a Phase 2 re-import does: recompute from the evidence cells as read back; edits outside review columns
        # change it, edits to review columns do not.
        for name in self.book.order[1:-1]:
            rows = self.book.rows(name)
            k = rows[1].index("Row fingerprint")
            for row in rows[2:]:
                row = row + [None] * (k + 1 - len(row))
                self.assertEqual(ex.fingerprint(row[:k]), row[k], name)
                edited = list(row[:k])
                edited[1] = (edited[1] or "") + " (edited)"
                self.assertNotEqual(ex.fingerprint(edited), row[k])

    def test_review_columns_editable_defaults(self):
        for name in ("Recommendations", "URL Inventory", "Broken Links"):
            rows = self.book.table(name)
            self.assertTrue(rows)
            self.assertEqual({r["Review status"] for r in rows}, {"Not started"})
            self.assertEqual({r["Review decision"] for r in rows}, {None})
        sheet = self.book.xml("Recommendations")
        self.assertIsNone(sheet.find("m:sheetProtection", NS))  # sort and filter stay usable
        lists = [d.find("m:formula1", NS).text for d in sheet.iter(f"{{{seo_xlsx.NS}}}dataValidation")]
        self.assertEqual(lists, ['"Approve,Reject,Defer,Needs information"', '"Not started,In review,Done"'])

    def test_provenance_retained(self):
        ev = self.book.table("Evidence & Sources")
        listed = {r["ID"] for r in ev if r["Record type"] == "observation"}
        refs = set()
        for name in self.book.order[1:-1]:
            for r in self.book.table(name):
                for col in ("Evidence references (observation IDs)", "Evidence references", "Evidence reference"):
                    refs.update(x for x in (r.get(col) or "").split(", ") if x)
        self.assertTrue(refs)
        self.assertEqual(refs - listed, set())
        db = self.db()
        artifacts = {r[0] for r in db.execute("SELECT source_sha256 FROM artifact WHERE run_id=?", (self.run_id,))}
        history = db.execute("SELECT history_sha256 FROM run WHERE run_id=?", (self.run_id,)).fetchone()[0]
        for oid in listed:
            sha = db.execute("SELECT source_sha256 FROM observation WHERE run_id=? AND observation_id=?",
                             (self.run_id, oid)).fetchone()[0]
            self.assertEqual(next(r["Source SHA-256"] for r in ev if r["ID"] == oid), sha)
        db.close()
        self.assertEqual({r["ID"] for r in ev if r["Record type"] == "artifact"}, artifacts)
        self.assertEqual(self.book.overview()["Evidence history SHA-256"], history)
        prior = [r for r in self.book.table("Metadata & H1") + self.book.table("Broken Links")
                 if r["Evidence class"] == "prior"]
        self.assertTrue(prior and all(r["Origin (prior analysis)"] for r in prior))

    def test_historical_labelling(self):
        ov = self.book.overview()
        self.assertTrue(ov["Evidence currency"].startswith("HISTORICAL SNAPSHOT"))
        self.assertIn("2026-11-23T22:59Z", ov["Observed window"])
        self.assertIn("None. Indexing is not inferred", ov["Search Console evidence in run"])
        for name in self.book.order[1:]:
            for r in self.book.table(name):
                self.assertNotIn("current", (r.get("Evidence type") or "").lower())
                self.assertIn("2026-11-23T22:59Z", r.get("Observed") or "")

    def test_no_session_material_or_formulas(self):
        for part in self.book.zip.namelist():
            raw = self.book.zip.read(part)
            for needle in (b"SC-Workspace ", b"signature", b"Authorization", b"Bearer", b"keyId", b"<f>", b"<f "):
                self.assertNotIn(needle, raw, part)

    def test_read_only(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        export(self.path, self.run_id, scope="filtered", flt="findings", query="a")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        db = seo_store.connect(self.path, readonly=True)
        conn = ex.readonly_connection(db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (y)")
        conn.close()
        db.close()

    def test_filter_consistency(self):
        db = self.db()
        for flt, sql in filters().items():
            data, name, counts = export(self.path, self.run_id, scope="filtered", flt=flt)
            book = Book(data)
            urls = {r[0] for r in db.execute(f"SELECT url FROM page WHERE run_id=? AND {sql}", (self.run_id,))}
            self.assertEqual({r["URL"] for r in book.table("URL Inventory")}, urls, flt)
            self.assertIn(f"page filter = {flt}", book.overview()["Scope"])
            self.assertIn("-filtered-", name)
            for sheet in ("Indexing Issues", "Metadata & H1"):
                if sheet in book.order:
                    self.assertLessEqual({r["URL"] for r in book.table(sheet)}, urls)
            if "Recommendations" in book.order:
                self.assertLessEqual({r["Subject"] for r in book.table("Recommendations")}, urls)
        data, _, _ = export(self.path, self.run_id, scope="filtered", query="/b/")
        self.assertEqual({r["URL"] for r in Book(data).table("URL Inventory")}, {ORIGIN + "/b/"})
        data, _, _ = export(self.path, self.run_id, scope="filtered", kind="redirect")
        recs = Book(data).table("Recommendations")
        self.assertEqual({r["Kind"] for r in recs}, {"redirect"})
        n = db.execute("SELECT COUNT(*) FROM recommendation WHERE run_id=? AND kind='redirect'", (self.run_id,)).fetchone()[0]
        self.assertEqual(len(recs), n)
        # scope=run ignores filter parameters
        whole, _, counts = export(self.path, self.run_id, scope="run", flt="not_200", query="zzz", kind="redirect")
        self.assertEqual(counts["URL Inventory"], self.counts["URL Inventory"])
        db.close()

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            export(self.path, self.run_id, scope="everything")
        with self.assertRaises(ValueError):
            export(self.path, self.run_id, scope="filtered", flt="drop table")
        with self.assertRaises(KeyError):
            export(self.path, "run_missing")

    @unittest.skipUnless(openpyxl, "openpyxl not installed (developer-only check)")
    def test_opens_in_openpyxl(self):
        wb = openpyxl.load_workbook(io.BytesIO(self.data))
        self.assertEqual(wb.sheetnames, self.book.order)
        ws = wb["URL Inventory"]
        self.assertEqual(ws.freeze_panes, "A3")
        self.assertEqual(ws.auto_filter.ref, f"A2:{seo_xlsx.col_letter(ws.max_column - 1)}{ws.max_row}")
        self.assertTrue(ws.cell(3, 2).hyperlink.target.startswith(ORIGIN))


class EvidenceShapes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.sqlite"
        seo_store.connect(self.path).close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_run(self):
        synthetic_run(self.path, "run_empty")
        data, _, counts = export(self.path, "run_empty")
        book = Book(data)
        self.assertEqual(book.order, ["Overview", "Recommendations", "URL Inventory", "Evidence & Sources"])
        self.assertEqual((counts["Recommendations"], counts["URL Inventory"], counts["Evidence & Sources"]), (0, 0, 1))
        omitted = [k for k in book.overview() if k.startswith("Omitted sheet:")]
        self.assertEqual(len(omitted), 5)
        ref = book.xml("URL Inventory").find("m:autoFilter", NS).get("ref")
        self.assertRegex(ref, r"^A2:[A-Z]+2$")  # header-only filter range on an empty sheet

    def test_unicode_and_hostile_evidence(self):
        url = ORIGIN + "/category/ünïcødé-中文/"
        synthetic_run(self.path, "run_u",
                      pages=[(url, {"measured_findings": 1}), (ORIGIN + "/=cmd/", {})],
                      observations=[("o1", url, "http_crawl", {"page_title": "=HYPERLINK(\"http://evil.invalid\")",
                                                               "h1": ["Ünïcødé 🌍", "@two"], "h1_count": 2})],
                      findings=[(url, "P2", "Review multiple H1 headings", "o1", "measured")],
                      recs=[("rid1", "headings", url, "+Reduce headings — “quoted” & <tags>")])
        book = Book(export(self.path, "run_u")[0])
        self.assertIn("Category Archives", book.order)
        cat = book.table("Category Archives")
        self.assertEqual((cat[0]["URL"], cat[0]["H1 text"], cat[0]["Page title"]),
                         (url, "Ünïcødé 🌍 | @two", "=HYPERLINK(\"http://evil.invalid\")"))
        self.assertEqual(book.table("Recommendations")[0]["Proposed action"], "+Reduce headings — “quoted” & <tags>")
        self.assertEqual(book.table("Metadata & H1")[0]["H1 text"], "Ünïcødé 🌍 | @two")
        for part in book.zip.namelist():
            self.assertNotIn(b"<f>", book.zip.read(part))

    def test_search_console_is_historical_and_dated(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            path, _, gsc_run = store(tmp.name, gsc=True)
            book = Book(export(path, gsc_run)[0])
            rows = book.table("Indexing Issues")
            sc = [r for r in rows if r["Evidence class"] == "search-console"]
            self.assertEqual(len(sc), 2)
            for r in sc:
                self.assertIn("Search Console", r["Evidence type"])
                self.assertIn("historical", r["Evidence type"])
                self.assertIn("pulled 2026-11-24T00:00:00Z", r["Observed"])
                self.assertTrue(r["Record ID"].startswith("S-"))
            self.assertTrue(any("reporting range 2026-10-01 to 2026-10-31" in r["Observed"] for r in sc))
            inv = book.table("URL Inventory")[0]
            self.assertEqual(inv["Search Console coverage (historical)"], "Submitted and indexed")
            self.assertIn("2 observations (historical", book.overview()["Search Console evidence in run"])
        finally:
            tmp.cleanup()

    def test_large_export(self):
        n = 20000
        pages = [(f"{ORIGIN}/p{i}/", {"measured_findings": 1, "broken_links_out": 1}) for i in range(n)]
        obs = [(f"o{i}", f"{ORIGIN}/p{i}/", "http_crawl", {"page_title": f"Page {i}", "h1_count": 2}) for i in range(n)]
        findings = [(f"{ORIGIN}/p{i}/", "P2", f"Repair broken link to {ORIGIN}/gone{i % 50}/", f"o{i}", "measured")
                    for i in range(n)]
        recs = [(f"r{i}", "link_repair", f"{ORIGIN}/p{i}/", "Repair link") for i in range(n)]
        synthetic_run(self.path, "run_big", pages=pages, observations=obs, findings=findings, recs=recs)
        started = time.monotonic()
        data, _, counts = export(self.path, "run_big")
        elapsed = time.monotonic() - started
        self.assertEqual((counts["URL Inventory"], counts["Recommendations"], counts["Broken Links"]), (n, n, n))
        self.assertEqual(counts["Evidence & Sources"], n + 1)
        self.assertLess(elapsed, 120)
        book = Book(data)
        self.assertRegex(book.xml("Broken Links").find("m:autoFilter", NS).get("ref"), rf"^A2:[A-Z]+{n + 2}$")
        self.assertLess(len(data), 40_000_000)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional Studio server dependency)")
class ExportEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        import dev_workspace_session as dev
        import seo_studio_server
        from seo_workspace_auth import Keyring
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path, cls.run_id, _ = store(cls.tmp.name)
        cls.key, cls.key_id, keyring = dev.make_signer()
        cls.dev = dev
        handler = seo_studio_server.make_handler(seo_store.connect(cls.path, readonly=True), Keyring(keyring),
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
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers

    def auth(self, subject="workspace-user:owner"):
        return self.dev.issue(self.key, self.key_id, subject)

    def test_requires_authorized_session(self):
        path = f"/api/export.xlsx?run={self.run_id}"
        self.assertEqual(self.get(path)[0], 401)
        self.assertEqual(self.get(path, self.auth("workspace-user:someone"))[0], 401)
        forged = json.loads(base64.urlsafe_b64decode(self.auth().split(" ")[1] + "=="))
        forged["subject"] = "workspace-user:other"
        self.assertEqual(self.get(path, "SC-Workspace " + base64.urlsafe_b64encode(json.dumps(forged).encode()).decode())[0],
                         401)

    def test_download(self):
        auth = self.auth()
        status, body, headers = self.get(f"/api/export.xlsx?run={self.run_id}", auth)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], ex.XLSX_TYPE)
        self.assertRegex(headers["Content-Disposition"], r'^attachment; filename="seo-studio-run_[0-9a-f]+-run-\d{8}T\d{6}Z\.xlsx"$')
        self.assertEqual(headers["Cache-Control"], "no-store")
        book = Book(body)
        self.assertEqual(book.overview()["Exported by (Workspace subject)"], "workspace-user:owner")
        self.assertNotIn(auth.split(" ")[1].encode()[:40], body)
        # filtered export agrees with the paginated pages API for every filter
        for flt in filters():
            _, pages, _ = self.get(f"/api/pages?run={self.run_id}&filter={flt}", auth)
            status, body, _ = self.get(f"/api/export.xlsx?run={self.run_id}&scope=filtered&filter={flt}", auth)
            self.assertEqual(status, 200)
            self.assertEqual(len(Book(body).table("URL Inventory")), json.loads(pages)["total"], flt)
        _, recs, _ = self.get(f"/api/recommendations?run={self.run_id}&kind=redirect", auth)
        _, body, _ = self.get(f"/api/export.xlsx?run={self.run_id}&scope=filtered&kind=redirect", auth)
        self.assertEqual(len(Book(body).table("Recommendations")), json.loads(recs)["total"])

    def test_bad_requests_and_read_only(self):
        auth = self.auth()
        self.assertEqual(self.get("/api/export.xlsx?run=nope", auth)[0], 400)
        self.assertEqual(self.get(f"/api/export.xlsx?run={self.run_id}&scope=all", auth)[0], 400)
        self.assertEqual(self.get(f"/api/export.xlsx?run={self.run_id}&scope=filtered&filter=x", auth)[0], 400)
        req = urllib.request.Request(self.base + f"/api/export.xlsx?run={self.run_id}", method="POST",
                                     headers={"Authorization": auth}, data=b"")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        self.assertEqual(err.exception.code, 501)


if __name__ == "__main__":
    unittest.main()
