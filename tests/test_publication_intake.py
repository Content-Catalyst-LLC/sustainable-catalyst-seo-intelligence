import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_publication_intake import intake


class IntakeTests(unittest.TestCase):
    def test_filters_protected_and_conflicting_rows(self):
        property_url = "https://example.test/"
        def record(slug, protected=False):
            return {"url": property_url + slug + "/", "exact_permalink": True,
                    "status": "publish", "publicly_viewable": True,
                    "password_protected": protected, "body": "discard this"}
        fixture = {"schema": "seo.wordpress-publication-inventory.v1", "property": property_url,
                   "observed_at_utc": "2026-11-20T12:00:00Z", "maps": [
            {"map_key": "a", "editorial_visible": True, "map": record("map-a"),
             "curated_articles": [record("article"), record("conflict"), record("protected", True)]},
            {"map_key": "b", "editorial_visible": True, "map": record("map-b"),
             "curated_articles": [record("conflict", True)]},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inventory.json"
            p.write_text(json.dumps(fixture))
            rows, receipt = intake(p, property_url, expected_maps=2)
            self.assertEqual(receipt["public_relationships"], 1)
            self.assertEqual(receipt["excluded_or_conflicting_urls"], 2)
            self.assertEqual(rows[0]["approved_map_urls"], [property_url + "map-a/"])
            self.assertNotIn("discard this", json.dumps(rows))
            with self.assertRaisesRegex(ValueError, "full expected"):
                intake(p, property_url)
