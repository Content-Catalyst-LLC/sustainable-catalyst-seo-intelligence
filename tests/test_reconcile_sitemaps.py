import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reconcile_sitemaps import publication_lookup, reconcile, sitemap_membership

ORIGIN = "https://sustainablecatalyst.com"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


class ReconciliationTests(unittest.TestCase):
    def test_missing_child_cannot_establish_absence(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            index = source / "index.xml"
            children = [f"child-{i}.xml" for i in range(12)]
            index.write_text(f"<sitemapindex {NS}>" + "".join(
                f"<sitemap><loc>{ORIGIN}/{name}</loc></sitemap>" for name in children
            ) + "</sitemapindex>")
            for name in children[1:]:
                (source / name).write_text(f'<urlset {NS}/>')
            first_leaf = source / children[0]
            record = {"url": ORIGIN + "/media/", "request": 9, "status": 200,
                      "body_sha256": "body", "headers_sha256": "headers",
                      "canonicals": [ORIGIN + "/support/"]}
            members, result = sitemap_membership(index, source)
            row = reconcile([record], members, result["complete"], {})[0]
            self.assertEqual(result["declared"], 12)
            self.assertEqual(row["sitemap_membership"], "unknown")
            self.assertIsNone(row["wordpress_status_from_offline_export"])
            first_leaf.write_text(f'<urlset {NS}><url><loc>{ORIGIN}/support/</loc></url></urlset>')
            members, result = sitemap_membership(index, source)
            row = reconcile([record], members, result["complete"], {record["url"]: "publish"})[0]
            self.assertTrue(result["complete"])
            self.assertEqual(row["sitemap_membership"], "absent")
            self.assertEqual(row["canonical_sitemap_membership"], "listed")
            self.assertEqual(row["canonical_status"], "different")
            self.assertEqual(row["wordpress_status_from_offline_export"], "publish")
            first_leaf.write_text(f'<!DOCTYPE urlset><urlset {NS}/>')
            _, result = sitemap_membership(index, source)
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["rejected"]), 1)

    def test_conflicting_publication_states_raise(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text('[{"url":"' + ORIGIN + '/","status":"draft"},'
                            '{"url":"' + ORIGIN + '/","status":"publish"}]')
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                publication_lookup(path)
