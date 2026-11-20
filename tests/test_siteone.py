"""Synthetic-only regression tests. Never fetch or execute external websites."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seo_siteone as m

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "siteone-synthetic-v2.json"
PROPERTY = "https://example.test/"
TIME = "2026-11-20T12:31:00Z"


class SiteOneTests(unittest.TestCase):
    def test_import_and_typed_evidence(self):
        a = m.parse_report(SAMPLE, PROPERTY, TIME)
        r = a["receipt"]
        self.assertEqual(r["accepted"], 4)
        self.assertEqual(r["rejected"], {"outside_property_or_invalid_url": 1})
        self.assertEqual(r["crawler_version_reported"], "2.5.1")
        self.assertFalse(r["crawler_timezone_verified"])
        self.assertTrue(r["does_not_prove_googlebot_or_indexing"])
        first = a["observations"][0]
        self.assertEqual(first["http_status"], 200)
        self.assertEqual(first["comparison_key"], "https://example.test/guide/?source=test")
        self.assertEqual(first["source_observed_at_raw"], "2026-11-20 12:30:00")
        self.assertIsNone(first["observed_at_utc"])
        self.assertIsNone(first["gsc_indexing_state"])
        self.assertIsNone(first["declared_canonical"])
        self.assertEqual(first["page_title"], "Synthetic guide")
        self.assertEqual(first["robots_index_raw"], "1")

    def test_redirect_is_single_hop_only(self):
        obs = m.parse_report(SAMPLE, PROPERTY, TIME)["observations"]
        old = next(o for o in obs if o["original_url"].endswith("/old/"))
        self.assertEqual(old["http_status"], 301)
        self.assertEqual(old["redirect_target_url"], "https://example.test/guide/?source=test")
        self.assertNotIn("final_url", old)

    def test_original_urls_preserved_and_queries_distinct(self):
        self.assertNotEqual(m.url_key("http://example.test/a?x=1", PROPERTY), m.url_key("http://example.test/a?x=2", PROPERTY))
        self.assertEqual(m.url_key("http://example.test/a?x=1", PROPERTY), "https://example.test/a?x=1")
        self.assertIsNone(m.url_key("https://example.test.evil.org/path", PROPERTY))
        self.assertIsNone(m.url_key("https://example.test:8888/", PROPERTY))
        self.assertIsNone(m.url_key("javascript:alert(1)", PROPERTY))
        self.assertIsNone(m.url_key("https://example.test/../admin/", PROPERTY))

    def test_import_receipt_and_reconcile_keep_google_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inventory = root / "inventory.csv"
            with inventory.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["url", "indexing_status", "http_status"])
                w.writeheader()
                w.writerow({"url": "https://example.test/guide/?source=test", "indexing_status":"Crawled – currently not indexed", "http_status": "200"})
            data = m.parse_report(SAMPLE, PROPERTY, TIME)
            rec = m.save_import(data, root / "out", inventory)
            self.assertEqual(rec["reconciled_inventory"]["rows"], 1)
            with (root / "out" / "seo-siteone-reconciled-inventory.csv").open(encoding="utf-8-sig") as f:
                r = next(csv.DictReader(f))
            self.assertEqual(r["indexing_status"], "Crawled – currently not indexed")
            self.assertEqual(r["siteone_http_status"], "200")
            self.assertNotEqual(rec["source_sha256"], rec["output_sha256"])

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "too-large.json"
            path.write_bytes(b"{" + b" " * 100)
            before = m.MAX_BYTES
            try:
                m.MAX_BYTES = 50
                with self.assertRaises(m.ImportErrorEvidence):
                    m.load_source(path)
            finally:
                m.MAX_BYTES = before

    def test_malformed_table_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            source = json.loads(SAMPLE.read_text())
            source["tables"]["seo"]["rows"] = {"urlPathAndQuery":"/foo/"}
            path.write_text(json.dumps(source))
            with self.assertRaises(m.ImportErrorEvidence):
                m.parse_report(path, PROPERTY, TIME)

    def test_missing_crawler_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            path.write_text(json.dumps({"results": []}))
            with self.assertRaises(m.ImportErrorEvidence):
                m.parse_report(path, PROPERTY, TIME)

    def test_invalid_status_rejected_without_guess(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            source = json.loads(SAMPLE.read_text())
            source["results"][0]["status"] = "bad"
            path.write_text(json.dumps(source))
            r = m.parse_report(path, PROPERTY, TIME)["receipt"]
            self.assertEqual(r["accepted"], 3)
            self.assertEqual(r["rejected"]["invalid_http_status"], 1)

    def test_no_timezone_invention(self):
        with self.assertRaises(m.ImportErrorEvidence):
            m.utc_timestamp("2026-11-20T12:31:00")
        self.assertEqual(m.utc_timestamp("2026-11-20T06:31:00-06:00"), TIME)

    def test_csv_formula_and_html_strings_not_executed(self):
        self.assertEqual(m.csv_safe("=HYPERLINK(\"bad\")"), "'=HYPERLINK(\"bad\")")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sample.json"
            source = json.loads(SAMPLE.read_text())
            source["tables"]["seo"]["rows"][0]["title"] = "</script><script>alert(1)</script>"
            path.write_text(json.dumps(source))
            data = m.parse_report(path, PROPERTY, TIME)
            m.save_import(data, Path(d) / "out")
            receipt = json.loads((Path(d) / "out" / "seo-siteone-import-receipt.json").read_text())
            self.assertEqual(receipt["accepted"], 4)
            self.assertEqual(data["observations"][0]["page_title"], "</script><script>alert(1)</script>")

    def test_duplicate_results_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "duplicate.json"
            obj = json.loads(SAMPLE.read_text())
            obj["results"].append(obj["results"][0])
            path.write_text(json.dumps(obj))
            result = m.parse_report(path, PROPERTY, TIME)
            self.assertEqual(result["receipt"]["accepted"], 5)
            self.assertTrue(result["receipt"]["warnings"])
            self.assertNotEqual(result["observations"][0]["observation_id"], result["observations"][-1]["observation_id"])

    def test_no_external_network_dependency(self):
        self.assertFalse(hasattr(m, "requests"))
        self.assertFalse(hasattr(m, "urlopen"))


if __name__ == "__main__":
    unittest.main()