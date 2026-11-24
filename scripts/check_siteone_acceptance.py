"""Assert the observed loopback facts and emit a checksum-only receipt."""
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, evidence):
    if not condition:
        raise SystemExit("Acceptance assertion failed: " + evidence)


summary = json.loads(Path("first/seo-summary.json").read_text())
history = summary["evidence_history"]
pages = {page["url"].rsplit("/", 1)[-1]: page for page in history["pages"]}
receipt = json.loads(Path("first/seo-siteone-import-receipt.json").read_text())
require(receipt["crawler_version_reported"] == "2.5.1.20260627", "binary build")
require(receipt["accepted"] == 7 and not receipt["rejected"], "accepted results")
require(history["crawl_http_counts"] == {"200": 4, "301": 1, "403": 1, "404": 1}, "HTTP counts")
require("denied" not in pages, "robots exclusion")
require(summary["coverage"]["indexed_reported"] is None, "indexing remains unknown")
require(any(o["facts"].get("page_h1") == "SEO fixture heading" for o in pages["guide"]["observations"]),
        "heading")
require(any(o["facts"].get("redirect_target_url") == "http://127.0.0.1:18765/guide"
            for o in pages["old"]["observations"]), "redirect target")
require(any(o["facts"].get("broken_link") == "http://127.0.0.1:18765/missing"
            for o in pages[""]["observations"]), "broken link source")
require(any(o["facts"].get("robots_index_raw") == "0" for o in pages["noindex"]["observations"]),
        "SiteOne noindex observation")
require("x-robots-tag: noindex" in Path("noindex.headers").read_text().lower(), "captured HTTP header")
actions = {item["action"] for item in pages["noindex"]["findings"]}
require(pages["noindex"]["sitemap_member"] and
        {"Review declared canonical", "Review noindex directive",
         "Review sitemap entry against technical findings", "Review missing primary heading"} <= actions,
        "canonical, noindex, sitemap and heading findings")
paths = ["siteone.json", "sitemap.xml", "noindex.html", "noindex.headers", "server.log",
         "first/seo-siteone-observations.json", "first/seo-summary.json", "first/seo-dashboard.html"]
Path("acceptance-receipt.json").write_text(json.dumps({
    "mode": "verified binary against disposable loopback fixture; byte-identical offline replay",
    "release": receipt["crawler_version_reported"],
    "archive_sha256": sys.argv[1],
    "license_sha256": sha("upstream-LICENSE"),
    "platform": sys.platform,
    "imported_at_utc": receipt["imported_at_utc"],
    "http_counts": history["crawl_http_counts"],
    "artifacts_sha256": {path: sha(path) for path in paths},
}, indent=2) + "\n")
print("SiteOne loopback acceptance passed")
