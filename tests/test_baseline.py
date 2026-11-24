import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

import seo_baseline
from seo_redirect_proposals import read_proposals, verify
from seo_structured_data import extract_blocks, findings, validate_blocks

FIXTURE = Path(__file__).parent / "fixtures" / "sc_seo_01_mini"
ORIGIN = "https://sustainablecatalyst.com"


def run_import(out):
    argv = ["--evidence-dir", str(FIXTURE), "--release-dir", str(FIXTURE / "release"),
            "--tool-dir", str(FIXTURE / "tools"), "--issue-groups", str(FIXTURE / "issue-groups.csv"),
            "--existing-redirects", str(FIXTURE / "release/plans/existing-redirects.txt"),
            "--output-dir", str(out), "--property", ORIGIN + "/", "--imported-at", "2026-11-24T14:00:00Z"]
    with redirect_stdout(io.StringIO()):
        code = seo_baseline.main(argv)
    return code, json.loads((out / "seo-baseline-receipt.json").read_text()), \
        json.loads((out / "seo-baseline-history.json").read_text())


class StructuredDataPort(unittest.TestCase):
    """Golden cases reproduce SC-SEO-01 ldcheck.py output fields (equivalence was also checked on 55 captured pages)."""

    def test_valid_graph(self):
        html = ('<script type="application/ld+json">{"@graph":[{"@type":"Organization","@id":"https://a/#organization"},'
                '{"@type":"WebSite","@id":"https://a/#website","name":"A","publisher":{"@id":"https://a/#organization"}}]}</script>')
        result = validate_blocks(extract_blocks(html))
        self.assertEqual(result["parse"], "ok")
        self.assertEqual(result["types"], ["Organization", "WebSite"])
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["publisher"], ["Organization"])
        self.assertEqual(result["website_name"], ["A"])
        self.assertEqual(findings(result), [])

    def test_unresolved_reference_and_missing_article_fields(self):
        result = validate_blocks(['{"@graph":[{"@type":"Article","@id":"https://a/x/#a","publisher":{"@id":"https://a/#gone"}}]}'])
        self.assertEqual(result["unresolved"], ["https://a/#gone"])
        self.assertEqual(result["article_missing"], ["author", "datePublished", "headline"])
        actions = [a for _, a in findings(result)]
        self.assertTrue(any(a.startswith("Resolve JSON-LD") for a in actions))

    def test_gravatar_and_fragmentless_ids_are_not_unresolved(self):
        result = validate_blocks(['{"@type":"Person","@id":"https://a/#p","image":{"@id":"https://gravatar.com/x#i"},"url":{"@id":"https://a/plain"}}'])
        self.assertEqual(result["unresolved"], [])

    def test_malformed_json_is_reported_not_raised(self):
        result = validate_blocks(['{"@type": "Article",'])
        self.assertTrue(result["parse"].startswith("ERROR"))
        self.assertEqual(findings(result)[0][0], "P1")

    def test_no_blocks_is_a_fact_not_a_defect(self):
        result = validate_blocks(extract_blocks("<html></html>"))
        self.assertEqual((result["blocks"], result["parse"]), (0, "ok"))
        self.assertEqual(findings(result), [])


class RedirectProposalPort(unittest.TestCase):
    OBS = {"/old/": {"status": 404, "canonicals": [], "noindex": False, "observation_id": "o1"},
           "/new/": {"status": 200, "canonicals": [ORIGIN + "/new/"], "noindex": False, "observation_id": "o2"},
           "/live/": {"status": 200, "canonicals": [ORIGIN + "/live/"], "noindex": False, "observation_id": "o3"},
           "/hidden/": {"status": 200, "canonicals": [ORIGIN + "/hidden/"], "noindex": True, "observation_id": "o4"},
           "/elsewhere/": {"status": 200, "canonicals": [ORIGIN + "/other/", ORIGIN + "/x/"], "noindex": False,
                           "observation_id": "o5"}}

    def check(self, rows, existing=()):
        text = "source,target\n" + "".join(f"{s},{t}\n" for s, t in rows)
        return verify(read_proposals(text), list(existing), self.OBS, ORIGIN)

    def test_pass(self):
        self.assertEqual(self.check([("/old/", "/new/")])[0]["verdict"], "pass")

    def test_each_original_rule(self):
        cases = {("/old/", "/old/"): "self-loop", ("/live/", "/new/"): "source currently 200",
                 ("/old/", "/hidden/"): "target noindex", ("/old/", "/elsewhere/"): "target canonical"}
        for pair, expected in cases.items():
            problems = "; ".join(self.check([pair])[0]["problems"])
            self.assertIn(expected, problems, pair)

    def test_first_canonical_is_used_like_the_original(self):
        result = self.check([("/old/", "/elsewhere/")])[0]
        self.assertEqual(result["target_canonical"], ORIGIN + "/other/")

    def test_chain_and_existing_rules_with_trailing_newlines(self):
        # The original tool kept the newline in existing rules, so this rule never fired; the port strips it.
        results = self.check([("/old/", "/new/"), ("/new/", "/live/")], existing=["/old/\n".strip()])
        self.assertIn("source already in Redirection", results[0]["problems"])
        self.assertIn("target is also a source (chain/loop)", results[0]["problems"])

    def test_unobserved_urls_stay_unknown(self):
        result = self.check([("/never-seen/", "/new/")])[0]
        self.assertEqual(result["verdict"], "unverified")
        self.assertIn("source not observed (unknown)", result["problems"])

    def test_rejects_absolute_or_missing_columns(self):
        with self.assertRaises(ValueError):
            read_proposals("source,target\nhttps://example.test/a/,/b/\n")
        with self.assertRaises(ValueError):
            read_proposals("from,to\n/a/,/b/\n")


class BaselineImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.code, self.receipt, self.history = run_import(Path(self.tmp.name) / "out")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reconciles_every_source_row(self):
        self.assertEqual(self.code, 0)
        self.assertTrue(self.receipt["reconciled"], self.receipt["reconciliation"])
        self.assertEqual(len(self.receipt["reconciliation"]), 9)
        rejected = self.receipt["rejected_rows"]
        self.assertEqual(sorted(r["source_kind"] for r in rejected), ["audit_finding", "http_crawl"])
        self.assertTrue(all("bad link" in r["url_repr"] for r in rejected))

    def test_no_fabricated_timestamps_and_window_recorded(self):
        crawl = [o for p in self.history["pages"] for o in p["observations"] if o["source_kind"] == "http_crawl"]
        self.assertTrue(crawl)
        self.assertTrue(all(o["source_time_raw"] is None for o in crawl))
        window = self.history["baseline"]["observed_window"]
        self.assertEqual((window["precision"], window["per_request_timestamps"]), ("run", False))

    def test_provenance_hashes_and_analysis_versions(self):
        for name, item in self.receipt["inputs"].items():
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$", name)
        self.assertIn("analyse.py", self.receipt["analysis_versions"])
        audit = [o for p in self.history["pages"] for o in p["observations"] if o["source_kind"] == "audit_finding"]
        self.assertTrue(all(o["facts"]["analysis"].startswith("sc-seo-01/analyse.py@") for o in audit))

    def test_broken_links_use_original_rule(self):
        edges = sorted((p["url"], o["facts"]["broken_link"]) for p in self.history["pages"] for o in p["observations"]
                       if "broken_link" in o["facts"])
        self.assertEqual(edges, [(ORIGIN + "/", ORIGIN + "/gone/"), (ORIGIN + "/", ORIGIN + "/old-slug/"),
                                 (ORIGIN + "/a/", ORIGIN + "/gone/"), (ORIGIN + "/b/", ORIGIN + "/gone/")])

    def test_measured_findings_and_prior_classifications_are_distinct(self):
        page = next(p for p in self.history["pages"] if p["url"] == ORIGIN + "/b/")
        measured = {f["action"] for f in page["findings"] if "origin" not in f}
        prior = [f for f in page["findings"] if "origin" in f]
        self.assertIn("Review multiple H1 headings", measured)
        self.assertIn("Add a meta description", measured)
        self.assertTrue(any(a.startswith("Resolve JSON-LD") for a in measured))
        self.assertEqual([f["action"] for f in prior], ["Missing meta description"])

    def test_governance_states_copied_never_upgraded(self):
        kb = [o["facts"] for p in self.history["pages"] for o in p["observations"]
              if o["source_kind"] == "wordpress" and o["facts"].get("verification_state")]
        self.assertEqual([f["verification_state"] for f in kb], ["not_verified"])
        self.assertEqual(self.receipt["counts"]["kb_governance_without_wordpress_status"], 1)  # id 99: no status, skipped
        self.assertEqual(self.history["baseline"]["kb_verification_states"], {"not_verified": 2})

    def test_redirect_and_change_event_records(self):
        rv = self.history["baseline"]["redirect_verification"]
        self.assertEqual(rv["approved"]["summary"]["pass"], 1)
        self.assertEqual(rv["held_for_review"]["results"][0]["verdict"], "fail")  # target is noindex
        self.assertEqual(self.history["change_events"][0]["state"], "validated-not-deployed")

    def test_replay_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as other:
            _, receipt, _ = run_import(Path(other) / "out")
        self.assertEqual(receipt["output_sha256"], self.receipt["output_sha256"])

    def test_refuses_existing_output_directory(self):
        with self.assertRaises(FileExistsError):
            run_import(Path(self.tmp.name) / "out")


if __name__ == "__main__":
    unittest.main()
