"""Entirely synthetic tests for SC-SEO-02. Never import production exports in CI."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seo_studio as seo

X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def make_book(path, kind, urls):
    """Synthetic Google-export-shaped workbook built using ZIP/XML."""
    header = '<?xml version="1.0" encoding="UTF-8"?>'
    sheets = [("Table", [["URL", "Last crawled"]] + [[u, "2026-10-01"] for u in urls]),
              ("Chart", [["Date", "Affected pages"], ["2026-11-15", len(urls)]])]
    if kind in ("unindexed", "403"):
        issue = "Crawled - currently not indexed" if kind == "unindexed" else "Blocked due to access forbidden (403)"
        sheets.append(("Metadata", [["Property", "Value"], ["Issue", issue]]))
    with zipfile.ZipFile(path, "w") as z:
        sheet_xml = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' %
                            (name, i, i) for i, (name, _) in enumerate(sheets, 1))
        z.writestr("xl/workbook.xml", header +
                   '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (X, R, sheet_xml))
        rels = "".join('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' %
                       (i, R, i) for i in range(1, len(sheets)+1))
        z.writestr("xl/_rels/workbook.xml.rels", header +
                   '<Relationships xmlns="%s">%s</Relationships>' % (P, rels))
        for i, (_, values) in enumerate(sheets, 1):
            rows = []
            for n, cells in enumerate(values, 1):
                cell_xml = "".join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' %
                                   (chr(65+j), n, value) for j, value in enumerate(cells))
                rows.append('<row r="%d">%s</row>' % (n, cell_xml))
            z.writestr("xl/worksheets/sheet%d.xml" % i,
                       header + '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (X, "".join(rows)))


class StudioTests(unittest.TestCase):
    def test_normalization_and_scope(self):
        self.assertEqual(seo.normalize("http://www.sustainablecatalyst.com/a"),
                         "https://sustainablecatalyst.com/a/")
        self.assertEqual(seo.normalize("https://attacker.invalid/a"), "")
        self.assertEqual(seo.normalize("javascript:alert(1)"), "")

    def test_url_types(self):
        self.assertEqual(seo.page_type("https://sustainablecatalyst.com/support/guide/"), "Knowledge Base")
        self.assertEqual(seo.page_type("https://sustainablecatalyst.com/test/feed/"), "Feed")
        self.assertEqual(seo.page_type("https://sustainablecatalyst.com/category/research/"), "Category archive")
        self.assertEqual(seo.page_type("https://sustainablecatalyst.com/wp-json/wp/v2/posts"), "REST API")

    def test_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "indexed.xlsx"
            make_book(p, "indexed", ["https://sustainablecatalyst.com/synthetic-a/"])
            sheets = seo.excel_rows(p)
            self.assertEqual(len(sheets["Table"]), 1)
            self.assertEqual(sheets["Table"][0]["URL"], "https://sustainablecatalyst.com/synthetic-a/")
            self.assertEqual(seo.source_type(sheets), "indexed")

    def test_reconcile_historical_vs_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            make_book(folder / "indexed.xlsx", "indexed",
                      ["https://sustainablecatalyst.com/synthetic-indexed/"])
            make_book(folder / "unindexed.xlsx", "unindexed",
                      ["https://sustainablecatalyst.com/synthetic-unindexed/"])
            make_book(folder / "blocked.xlsx", "403",
                      ["https://sustainablecatalyst.com/synthetic-blocked/"])
            with (folder / "SC-SEO-01-live-check.tsv").open("w", encoding="utf-8", newline="") as stream:
                w = csv.writer(stream, delimiter="\t")
                w.writerow(["url", "http_status", "final_url", "redirect_count"])
                w.writerow(["https://sustainablecatalyst.com/synthetic-blocked/", "200",
                            "https://sustainablecatalyst.com/synthetic-blocked/", "0"])
            report = seo.read_sources(folder, None)
            self.assertEqual(len(report["inventory"]), 3)
            self.assertEqual(report["coverage"]["indexed_export_count"], 1)
            self.assertEqual(report["coverage"]["unindexed_examples"], 1)
            self.assertEqual(report["http_audit"]["status_counts"], {"200": 1})
            self.assertTrue(report["http_audit"]["does_not_prove_googlebot_behavior"])
            self.assertIn("Historical 403", [r["indexing_status"] for r in report["inventory"]])
            seo.save_report(report, folder / "generated")
            self.assertTrue((folder / "generated" / "seo-dashboard.html").is_file())
            self.assertTrue((folder / "generated" / "seo-url-inventory.csv").is_file())

    def test_quality_scoring_is_not_fabricated(self):
        row = {"type": "Article / page",
               "indexing_status": "Crawled – currently not indexed", "http_status": ""}
        priority, action = seo.action_for(row)
        self.assertEqual(priority, "P2")
        self.assertNotIn("low quality", action.lower())

    def test_historical_403_does_not_become_current_403(self):
        row = {"type": "Article / page", "indexing_status": "Historical 403", "http_status": "200"}
        self.assertEqual(seo.action_for(row)[0], "P3")

    def test_html_script_is_escaped(self):
        payload = {"payload": "</script><script>alert(1)</script>"}
        rendered = seo.dashboard(payload)
        self.assertNotIn("</script><script>alert(1)</script>", rendered)
        self.assertIn("\\u003c/script", rendered)


if __name__ == "__main__":
    unittest.main()
