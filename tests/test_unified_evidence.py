"""Offline tests using synthetic imports and a projection of genuine SiteOne JSON."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import seo_studio as studio
from seo_siteone import ImportErrorEvidence, parse_report, url_key
from seo_evidence import EvidenceHistory
from test_seo_studio import make_book

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "fixtures" / "siteone-2.5.1-loopback-projection.json"
ORIGIN = "http://127.0.0.1:18765"
TIME = "2026-11-20T18:00:00Z"


class UnifiedEvidenceTests(unittest.TestCase):
    def test_binary_projection_relationships_and_duplicate_replay(self):
        parsed = parse_report(REAL, ORIGIN, TIME)
        history = EvidenceHistory(ORIGIN, TIME)
        history.siteone(parsed)
        history.siteone(parsed)
        result = history.build()
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertEqual(result["crawl_http_counts"], {"200": 4, "403": 1, "301": 1, "404": 1})
        pages = {p["url"]: p for p in result["pages"]}
        self.assertIn("broken_link", pages[ORIGIN + "/"]["observations"][-2]["facts"])
        self.assertEqual(pages[ORIGIN + "/old"]["observations"][0]["facts"]["redirect_target_url"], ORIGIN + "/guide")
        self.assertNotIn(ORIGIN + "/denied", pages)

    def test_cli_preserves_sources_and_generates_identical_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xlsx = root / "unindexed.xlsx"
            make_book(xlsx, "unindexed", [ORIGIN + "/guide"])
            http = root / "http.tsv"
            http.write_text(f"url\thttp_status\tobserved_at\n{ORIGIN}/guide\t503\t2026-10-31\n"
                            f"{ORIGIN}/guide\tbad\t2026-10-31\n")
            html = root / "response.html"
            html.write_text('<head><link rel="canonical" href="/guide">'
                            '<meta name="robots" content="noindex"></head>')
            sitemap = root / "sitemap.xml"
            sitemap.write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                               f'<url><loc>{ORIGIN}/noindex</loc></url></urlset>')
            wp = root / "wp.json"
            wp.write_text(json.dumps([
                {"publication_id": "</script><script>bad()</script>", "url": ORIGIN + "/guide",
                 "status": "publish", "access_token": "must-never-appear"},
                {"url": ORIGIN + "/missing"},
                {"publication_id": "2", "url": "https://outside.test/", "status": "publish"},
            ]))
            args = ["--input-dir", str(root), "--property", ORIGIN, "--http-tsv", str(http),
                    "--siteone-json", str(REAL), "--html-response", ORIGIN + "/noindex", str(html),
                    "--sitemap-xml", str(sitemap), "--publications-json", str(wp), "--imported-at", TIME]
            with contextlib.redirect_stdout(io.StringIO()):
                for run in ("a", "b"):
                    self.assertEqual(studio.main(args + ["--output-dir", str(root / run)]), 0)
            for path in (root / "a").iterdir():
                self.assertEqual(path.read_bytes(), (root / "b" / path.name).read_bytes())
            summary = json.loads((root / "a/seo-summary.json").read_text())
            history = summary["evidence_history"]
            pages = {p["url"]: p for p in history["pages"]}
            rows = {r["source_kind"]: r for r in pages[ORIGIN + "/guide"]["observations"]}
            self.assertEqual(rows["gsc_export"]["facts"]["export_classification"], "unindexed")
            self.assertEqual(rows["gsc_export"]["source_sha256"], hashlib.sha256(xlsx.read_bytes()).hexdigest())
            self.assertEqual(rows["historical_http"]["facts"]["http_status"], 503)
            self.assertEqual(rows["historical_http"]["source_time_raw"], "2026-10-31")
            self.assertEqual(rows["siteone"]["facts"]["http_status"], 200)
            self.assertEqual(summary["history"][0]["crawled_not_indexed"], 1)
            self.assertEqual(history["rejected"]["wordpress"], 2)
            self.assertEqual(history["rejected"]["historical_http"], 1)
            self.assertIs(pages[ORIGIN + "/noindex"]["sitemap_member"], True)
            self.assertIn("Review declared canonical",
                          [f["action"] for f in pages[ORIGIN + "/noindex"]["findings"]])
            dashboard = (root / "a/seo-dashboard.html").read_text()
            self.assertNotIn("must-never-appear", dashboard)
            self.assertNotIn("</script><script>bad()", dashboard)

    def test_scope_and_csv_formula_guards(self):
        for url in ("https://example.test:80/a", "http://example.test:443/a",
                    "https://@example.test/a", "https://example.test/a/%2e%2e/b",
                    "https://example.test/a\\b", " https://example.test/a"):
            self.assertIsNone(url_key(url, "https://example.test/"), url)
        self.assertIsNone(url_key("http://127.0.0.1:18766/", ORIGIN))
        self.assertEqual(url_key("https://example.test/a%2Fb?q=A", "https://example.test/"),
                         "https://example.test/a%2Fb?q=A")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            studio.write_csv(path, [{"cell": " \t=1+1"}], ["cell"])
            with path.open(encoding="utf-8-sig") as stream:
                self.assertEqual(next(csv.DictReader(stream))["cell"], "' \t=1+1")

    def test_reject_sitemap_dtd_and_out_of_scope_html(self):
        history = EvidenceHistory(ORIGIN, TIME)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sitemap.xml"
            path.write_text('<!DOCTYPE urlset [<!ENTITY a SYSTEM "file:///private.txt">]><urlset/>')
            with self.assertRaises(ImportErrorEvidence):
                history.sitemap(path)
            with self.assertRaises(ImportErrorEvidence):
                history.html_response("https://outside.test/noindex", path)

    def test_invalid_redirect_target_does_not_abort_import(self):
        raw = json.loads(REAL.read_text())
        raw["tables"]["redirects"]["rows"][0]["targetUrl"] = "http://[malformed"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-target.json"
            path.write_text(json.dumps(raw))
            parsed = parse_report(path, ORIGIN, TIME)
        old = next(row for row in parsed["observations"] if row["original_url"].endswith("/old"))
        self.assertIsNone(old["redirect_target_url"])
