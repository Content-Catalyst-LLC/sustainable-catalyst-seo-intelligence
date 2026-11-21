"""Make a privacy-filtered Studio input from an approved, offline WP-CLI inventory."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from seo_evidence import source_bytes
from seo_siteone import property_origin, url_key, utc_timestamp


def intake(source, property_url, expected_maps=170):
    raw = source_bytes(source)
    document = json.loads(raw)
    scope = property_origin(property_url)
    if not isinstance(document, dict) or document.get("schema") != "seo.wordpress-publication-inventory.v1" or document.get("property") != scope:
        raise ValueError("Expected a matching property and a WordPress inventory v1 export")
    observed = document.get("observed_at_utc")
    if not isinstance(observed, str):
        raise ValueError("Expected an inventory observation timestamp")
    observed = utc_timestamp(observed)
    maps = document.get("maps")
    if not isinstance(maps, list) or len(maps) != expected_maps:
        raise ValueError("Inventory must contain the full expected registry")
    seen_keys = set()
    candidates = defaultdict(list)
    relationships = set()

    def register(row, visible):
        if not isinstance(row, dict):
            raise ValueError("Unexpected publication record")
        raw_url = row.get("url")
        key = url_key(raw_url, scope)
        if key is None or key != raw_url or urlsplit(key).query:
            return None
        eligible = (visible and row.get("status") == "publish"
                    and row.get("exact_permalink") is True
                    and row.get("publicly_viewable") is True
                    and row.get("password_protected") is False)
        candidates[key].append(eligible)
        return key

    for row in maps:
        if not isinstance(row, dict):
            raise ValueError("Unexpected map row")
        key = row.get("map_key")
        articles = row.get("curated_articles")
        if not isinstance(key, str) or not key or key in seen_keys or not isinstance(articles, list):
            raise ValueError("Duplicate map identity or incomplete map row")
        seen_keys.add(key)
        visible = row.get("editorial_visible") is True
        map_url = register(row.get("map"), visible)
        for article in articles:
            article_url = register(article, visible)
            if map_url and article_url and article_url != map_url:
                relationships.add((map_url, article_url))
    eligible = {url for url, checks in candidates.items() if all(checks)}
    approved = sorted((m, a) for m, a in relationships if {m, a} <= eligible)
    article_to_maps = defaultdict(list)
    for m, a in approved:
        article_to_maps[a].append(m)
    rows = [{"publication_id": hashlib.sha256(url.encode()).hexdigest(), "url": url,
             "status": "publish", "publicly_viewable": True, "password_protected": False,
             "exact_permalink": True, "approved_map_urls": article_to_maps[url],
             "inventory_observed_at": observed}
            for url in sorted(eligible)]
    return rows, {"input_sha256": hashlib.sha256(raw).hexdigest(), "observed_at_utc": observed,
                  "map_rows": len(maps), "public_endpoints": len(rows),
                  "public_relationships": len(approved), "excluded_or_conflicting_urls": len(candidates) - len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--property", default="https://sustainablecatalyst.com/")
    args = parser.parse_args()
    rows, receipt = intake(args.source, args.property)
    args.output.mkdir(mode=0o700, exist_ok=False)
    rendered = json.dumps(rows, indent=2) + "\n"
    (args.output / "studio-publications.json").write_text(rendered)
    receipt["output_sha256"] = hashlib.sha256(rendered.encode()).hexdigest()
    (args.output / "intake-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    import os
    os.umask(0o077)
    main()
