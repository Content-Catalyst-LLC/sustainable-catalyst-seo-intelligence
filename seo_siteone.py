#!/usr/bin/env python3
"""SC-SEO-02.1: read-only, offline SiteOne JSON evidence ingestion.

Compatible with SiteOne's documented ``crawler`` / ``results`` / ``tables``
JSON layout. No network requests, subprocess execution, or Googlebot claims.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

SCHEMA = "seo.url-observation.v1"
ADAPTER = "0.1.0"
MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_URL = 4096


class ImportErrorEvidence(ValueError):
    """Input cannot be safely interpreted without guessing."""


def utc_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportErrorEvidence("--imported-at must be ISO-8601 with a timezone") from exc
    if result.tzinfo is None:
        raise ImportErrorEvidence("--imported-at must include an explicit timezone")
    return result.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def property_host(property_url: str) -> str:
    try:
        parts = urlsplit(property_url)
        host = parts.hostname
        if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
            raise ImportErrorEvidence("Property must be an HTTPS origin without credentials or a nonstandard port")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ImportErrorEvidence("Property must be an HTTPS origin with no path/query/fragment")
        return host.lower()
    except ValueError as exc:
        raise ImportErrorEvidence("Invalid property URL") from exc


def url_key(url: str, property_url: str) -> str | None:
    """Conservative URL comparison key: keep path/query case and percent encodings.

    Only normalize scheme, host and the default root path. Never drop query strings.
    """
    if not isinstance(url, str) or not url or len(url) > MAX_URL or any(ord(c) < 32 for c in url):
        return None
    try:
        u = urlsplit(url)
        host = property_host(property_url)
        if u.scheme.lower() not in ("http", "https") or not u.hostname or u.hostname.lower() != host:
            return None
        if u.username or u.password or u.port not in (None, 80, 443):
            return None
        # Exclude fragments from comparison; queries remain meaningful.
        path = u.path or "/"
        if not path.startswith("/") or re.search(r"/(?:\.\.?)(?:/|$)", path):
            return None
        return urlunsplit(("https", host, path, u.query, ""))
    except (ValueError, ImportErrorEvidence):
        return None


def load_source(path: Path) -> tuple[dict, str]:
    if not path.is_file():
        raise ImportErrorEvidence("SiteOne input file not found")
    if path.stat().st_size > MAX_BYTES:
        raise ImportErrorEvidence(f"SiteOne report exceeds {MAX_BYTES} byte ingestion limit")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        obj = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImportErrorEvidence("Invalid UTF-8 SiteOne JSON") from exc
    if not isinstance(obj, dict):
        raise ImportErrorEvidence("SiteOne report must be a JSON object")
    if not isinstance(obj.get("crawler"), dict) or not isinstance(obj.get("results"), list):
        raise ImportErrorEvidence("Missing SiteOne crawler/results object")
    if len(obj["results"]) > MAX_RECORDS:
        raise ImportErrorEvidence("Too many URL results")
    return obj, sha


def table_rows(source: dict, key: str) -> list:
    tables = source.get("tables")
    if tables is None:
        return []
    if not isinstance(tables, dict):
        raise ImportErrorEvidence("SiteOne tables must be a JSON object")
    table = tables.get(key)
    if table is None:
        return []
    if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
        raise ImportErrorEvidence(f"SiteOne table {key} must contain rows list")
    if len(table["rows"]) > MAX_RECORDS:
        raise ImportErrorEvidence(f"SiteOne table {key} is too large")
    return table["rows"]


def relative_row_url(value: object, property_url: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return url_key(property_url.rstrip("/") + value, property_url)
    return url_key(value, property_url)


def parse_report(path: Path, property_url: str, imported_at: str | None = None) -> dict:
    """Parse one immutable local report; do not assert crawl time zone.

    Source executedAt has a *naive local* time in upstream JSON docs and is
    intentionally retained as raw. import time UTC is different evidence.
    """
    host = property_host(property_url)
    property_url = f"https://{host}/"
    imported_utc = utc_timestamp(imported_at)
    data, sha = load_source(path)
    crawler = data["crawler"]
    if crawler.get("name") != "SiteOne Crawler":
        raise ImportErrorEvidence("Unexpected crawler name; refusing to interpret as SiteOne")
    if not isinstance(crawler.get("version"), str) or not crawler["version"]:
        raise ImportErrorEvidence("SiteOne crawler version is required")
    executed_raw = crawler.get("executedAt")
    if executed_raw is not None and not isinstance(executed_raw, str):
        raise ImportErrorEvidence("SiteOne executedAt must be string or null")

    seo_by_key: dict[str, dict] = {}
    redirects: dict[str, str] = {}
    warnings: list[str] = []
    for row in table_rows(data, "seo"):
        if not isinstance(row, dict):
            raise ImportErrorEvidence("Invalid SiteOne seo row")
        key = relative_row_url(row.get("urlPathAndQuery"), property_url)
        if key:
            if key in seo_by_key:
                warnings.append("Repeated SiteOne SEO metadata URL: " + key)
            seo_by_key[key] = row
    for row in table_rows(data, "redirects"):
        if not isinstance(row, dict):
            raise ImportErrorEvidence("Invalid SiteOne redirect row")
        key = relative_row_url(row.get("url"), property_url)
        target = relative_row_url(row.get("targetUrl"), property_url)
        # External redirect targets are retained only when absolute HTTP(S) URL,
        # never treated as crawled in-scope pages.
        external_target = row.get("targetUrl")
        if key and not target and isinstance(external_target, str):
            u = urlsplit(external_target)
            if u.scheme in ("http", "https") and u.hostname and not (u.username or u.password):
                target = external_target
        if key and target:
            redirects[key] = target

    observations = []
    rejected = Counter()
    seen = set()
    for index, row in enumerate(data["results"]):
        if not isinstance(row, dict):
            rejected["non_object"] += 1
            continue
        original = row.get("url")
        key = url_key(original, property_url)
        if not key:
            rejected["outside_property_or_invalid_url"] += 1
            continue
        status = row.get("status")
        if status is not None:
            if isinstance(status, bool) or str(status) not in {str(n) for n in range(100, 600)}:
                rejected["invalid_http_status"] += 1
                continue
            status = int(status)
        if key in seen:
            warnings.append("Repeated result URL preserved: " + key)
        seen.add(key)
        meta = seo_by_key.get(key, {})
        if not isinstance(meta, dict):
            meta = {}
        # SiteOne metadata fields are reported verbatim; robotsIndex '1' does
        # NOT become a Google indexing assertion or fabricated canonical.
        ob = {
            "schema_version": SCHEMA,
            "observation_id": hashlib.sha256(f"{sha}:{index}:{original}".encode("utf-8")).hexdigest(),
            "source_kind": "siteone",
            "source_method": "ordinary_siteone_http_crawl",
            "source_artifact_sha256": sha,
            "imported_at_utc": imported_utc,
            "source_observed_at_raw": executed_raw,
            "observed_at_utc": None,  # Upstream time lacks UTC offset
            "original_url": original,
            "comparison_key": key,
            "property_scope": property_url,
            "result_state": "observed_http" if status is not None else "unknown_http",
            "http_status": status,
            "redirect_target_url": redirects.get(key),  # a hop, not proven final URL
            "content_type_code": row.get("type") if isinstance(row.get("type"), int) and not isinstance(row.get("type"), bool) else None,
            "declared_canonical": None,  # not present in documented results/seo rows
            "robots_index_raw": meta.get("robotsIndex"),
            "robots_indexing_raw": meta.get("indexing"),
            "robots_denied_raw": meta.get("deniedByRobotsTxt"),
            "sitemap_membership": None,
            "page_title": meta.get("title") if isinstance(meta.get("title"), str) else None,
            "page_description": meta.get("description") if isinstance(meta.get("description"), str) else None,
            "page_h1": meta.get("h1") if isinstance(meta.get("h1"), str) else None,
            "review_status": "not_reviewed",
            "gsc_indexing_state": None,
        }
        observations.append(ob)
    receipt = {
        "contract": SCHEMA,
        "adapter_version": ADAPTER,
        "source_kind": "siteone",
        "source_file": path.name,
        "source_sha256": sha,
        "crawler_version_reported": crawler["version"],
        "crawler_executed_at_raw": executed_raw,
        "crawler_timezone_verified": False,
        "imported_at_utc": imported_utc,
        "property_scope": property_url,
        "input_results": len(data["results"]),
        "accepted": len(observations),
        "rejected": dict(rejected),
        "warnings": warnings,
        "status_counts": dict(Counter(str(row["http_status"]) if row["http_status"] is not None else "unknown" for row in observations)),
        "does_not_prove_googlebot_or_indexing": True,
    }
    return {"receipt": receipt, "observations": observations}


def csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    # CSV formula injection when an exported cell begins with spreadsheet syntax.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_csv(path: Path, rows: list[dict], columns: list[str]):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(row.get(k)) for k in columns})


def reconcile_inventory(observations: list[dict], inventory_path: Path, property_url: str) -> list[dict]:
    """Join with previous Studio CSV, retaining past GSC and present SiteOne separately."""
    latest = defaultdict(list)
    for ob in observations:
        latest[ob["comparison_key"]].append(ob)
    rows = []
    with inventory_path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            key = url_key(row.get("url"), property_url)
            if not key:
                continue
            obs = latest.get(key, [])
            if not obs:
                rows.append({**row, "siteone_http_status": "", "siteone_observation_count": 0,
                             "siteone_source_sha256": "", "siteone_raw_crawl_time": "", "siteone_redirect_target": ""})
            else:
                for ob in obs:
                    rows.append({**row, "siteone_http_status": ob["http_status"],
                                 "siteone_observation_count": len(obs),
                                 "siteone_source_sha256": ob["source_artifact_sha256"],
                                 "siteone_raw_crawl_time": ob["source_observed_at_raw"],
                                 "siteone_redirect_target": ob["redirect_target_url"]})
    return rows


def save_import(data: dict, output_dir: Path, inventory: Path | None = None):
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data["observations"], ensure_ascii=False, indent=2, allow_nan=False)
    observations_path = output_dir / "seo-siteone-observations.json"
    observations_path.write_text(raw + "\n", encoding="utf-8")
    receipt = data["receipt"].copy()
    receipt["output_sha256"] = hashlib.sha256(observations_path.read_bytes()).hexdigest()
    receipt["reconciled_inventory"] = None
    if inventory is not None:
        rows = reconcile_inventory(data["observations"], inventory, receipt["property_scope"])
        cols = list(rows[0]) if rows else ["url", "indexing_status", "siteone_http_status"]
        out = output_dir / "seo-siteone-reconciled-inventory.csv"
        write_csv(out, rows, cols)
        receipt["reconciled_inventory"] = {"filename": out.name, "rows": len(rows), "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    (output_dir / "seo-siteone-import-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Offline read-only SiteOne JSON importer for SEO Intelligence")
    p.add_argument("--siteone-json", type=Path, required=True, help="A local SiteOne --output-json-file report")
    p.add_argument("--property", default="https://sustainablecatalyst.com/", help="Exact HTTPS origin allowlist")
    p.add_argument("--output-dir", type=Path, required=True, help="PRIVATE output directory outside public Git repository")
    p.add_argument("--studio-inventory", type=Path, help="Optional prior seo-url-inventory.csv to reconcile without overwriting Google statuses")
    p.add_argument("--imported-at", help="Explicit timezone-aware ISO 8601 import time for reproducible tests")
    args = p.parse_args(argv)
    try:
        data = parse_report(args.siteone_json, args.property, args.imported_at)
        receipt = save_import(data, args.output_dir, args.studio_inventory)
    except (ImportErrorEvidence, OSError, csv.Error, UnicodeError) as exc:
        p.error(str(exc))
    print("SC-SEO-02.1 SiteOne import — read only")
    print("Accepted:", receipt["accepted"], "Rejected:", receipt["rejected"])
    print("Source SHA-256:", receipt["source_sha256"])
    print("Output:", args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())