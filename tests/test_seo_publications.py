"""Offline publication navigation regression tests."""
import json
from pathlib import Path
import tempfile
import unittest

from seo_publications import analyze_publications
from seo_siteone import ImportErrorEvidence

ORIGIN = "https://example.test/"
HUB = ORIGIN + "publications/"


class PublicationsTests(unittest.TestCase):
    def test_static_links_rendered_links_and_google_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub, article_map, rendered = (root / name for name in ("hub.html", "map.html", "rendered.html"))
            field = {"field": {"key": "systems", "browse_url": "/category/systems/", "panels": [
                {"key": "map", "canonical_url": "/map/", "articles": [
                    {"url": "/a/"}, {"url": "/b/"}, {"url": "/c/"}, {"url": "https://outside.test/d/"}]},
            ]}}
            hub.write_text(
                '<head><link rel="canonical" href="/publications/"></head><main>'
                '<a href="/map/">Map</a><a href="?sc_publication_panel=map#top">Switch</a>'
                '<a href="/a/">Article A</a><template><a href="/c/">Template</a></template>'
                '<script type="application/json">' + json.dumps(field) + '</script></main>')
            article_map.write_text('<main><a href="/b/">Article B</a></main>')
            rendered.write_text('<main><a href="/c/">Article C</a></main>')
            evidence = {"property_scope": ORIGIN, "pages": [{
                "url": ORIGIN + "b/", "sitemap_member": True, "observations": [{
                    "source_kind": "gsc_export", "facts": {"export_classification": "unindexed"},
                    "observation_id": "google-row",
                }],
            }]}
            result = analyze_publications([(HUB, hub), (ORIGIN + "map/", article_map)],
                                          [(HUB, rendered)], evidence)
            rows = {row["url"]: row for row in result["articles"]}
            a, b, c = (rows[ORIGIN + key + "/"] for key in "abc")
            self.assertTrue(a["hub_main_anchor"])
            self.assertTrue(b["reachable_in_captured_graph"])
            self.assertFalse(b["hub_main_anchor"])
            self.assertEqual(b["google_export_classes"], ["unindexed"])
            self.assertTrue(b["sitemap_member"])
            self.assertTrue(c["rendered_hub_anchor"])
            self.assertFalse(c["reachable_in_captured_graph"])
            self.assertIsNone(c["sitemap_member"])
            self.assertEqual(result["parameter_variants"][0]["parameters"], ["sc_publication_panel"])
            self.assertEqual(result["maps"][0]["articles_linked_from_map_snapshot"], [ORIGIN + "b/"])

    def test_unrecognized_and_duplicate_snapshots_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "hub.html"
            page.write_text("<main><a href='/article/'>Article</a></main>")
            evidence = {"property_scope": ORIGIN, "pages": []}
            with self.assertRaisesRegex(ImportErrorEvidence, "No recognized"):
                analyze_publications([(HUB, page)], [], evidence)
            with self.assertRaisesRegex(ImportErrorEvidence, "distinct"):
                analyze_publications([(HUB, page), (HUB, page)], [], evidence)
