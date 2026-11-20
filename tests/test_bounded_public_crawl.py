import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bounded_public_crawl import Collector, ORIGIN, Robots, Stop, in_scope, path_of


class CrawlBoundaryTests(unittest.TestCase):
    def test_scope(self):
        for url in (ORIGIN + "/support/guides/x?test=1", ORIGIN + "/support/guides/x?",
                    ORIGIN + "/support/guides/%2e%2e/x", "https://other.test/support/guides/a",
                    ORIGIN + "/support/guides/a/../wp-admin", ORIGIN + "//media/"):
            self.assertFalse(in_scope(path_of(url)))
        self.assertTrue(in_scope(path_of(ORIGIN + "/support/guides/a/")))
        self.assertTrue(in_scope(path_of(ORIGIN + "/media/")))
        self.assertFalse(in_scope(path_of(ORIGIN + "/media")))

    def test_robots_longest_and_wildcard(self):
        rules = Robots(["User-agent: *", "Disallow: /support/", "Allow: /support/guides/",
                        "Disallow: /support/guides/*/draft$"])
        self.assertTrue(rules.can_fetch("crawler", ORIGIN + "/support/guides/a"))
        self.assertFalse(rules.can_fetch("crawler", ORIGIN + "/support/guides/a/draft"))
        self.assertFalse(rules.can_fetch("crawler", ORIGIN + "/support/private"))
        with self.assertRaises(Stop):
            Robots(["User-agent: *", "Disallow: /%ZZ"])

    def test_guard_blocks_before_transport(self):
        collector = Collector(Path("."))
        with patch("bounded_public_crawl.subprocess.run") as transport:
            with self.assertRaises(Stop):
                collector.fetch("/", "page")
            collector.robots = Robots(["User-agent: *"])
            with self.assertRaises(Stop):
                collector.fetch("/wp-admin/", "page")
            collector.prefix_paths.update(str(i) for i in range(15))
            self.assertIsNone(collector.fetch("/support/guides/a", "page"))
            transport.assert_not_called()


class ReportTests(unittest.TestCase):
    def test_confirmed_broken_link_uses_both_capture_hashes(self):
        import json
        import tempfile
        from bounded_public_crawl import sha
        from public_crawl_report import report

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for n, (path, status, body) in enumerate((
                ("/", 200, b'<head><title>Test</title></head><a href="/media/">Missing</a>'
                           b'<a href="/outside/">Unknown</a>'),
                ("/media/", 404, b"Missing"),
            ), 1):
                name = f"{n}.body"
                (root / name).write_bytes(body)
                rows.append({"request": n, "kind": "page", "path": path, "url": ORIGIN + path,
                             "status": status, "method": "GET", "content_type": "text/html",
                             "body": name, "body_sha256": sha(body), "headers": "", "headers_sha256": sha(b""),
                             "start_offset_seconds": 4 * n, "end_offset_seconds": 4 * n + 1})
                rows[-1]["headers"] = f"{n}.headers"
                (root / rows[-1]["headers"]).touch()
            (root / "sc-seo-02-public-crawl-manifest.json").write_text(json.dumps({
                "requests": rows, "request_cap": 50, "page_cap": 40, "prefix_cap": 15,
                "duration_seconds": 9, "max_duration_seconds": 300, "minimum_gap_seconds": 3,
                "stop_reason": None, "started_at": "2026-11-20T00:00:00Z",
                "finished_at": "2026-11-20T00:00:09Z", "robots_verified": True,
            }))
            fixture = root / "prior-loopback.json"
            fixture.write_text('{"http_counts":{"200":4,"404":1}}')
            with patch("public_crawl_report.replay", return_value={"status": "test"}):
                report(root, root / "binary", fixture)
            findings = json.loads((root / "sc-seo-02-technical-findings.json").read_text())
            broken = next(a for a in findings["priority_tasks"] if "confirmed missing" in a["action"])
            self.assertEqual(len(broken["evidence"]), 2)
            self.assertEqual(findings["public_http"][0]["unchecked_internal_link_count"], 1)
            self.assertEqual(findings["public_http"][0]["broken_internal_links"], [ORIGIN + "/media/"])
