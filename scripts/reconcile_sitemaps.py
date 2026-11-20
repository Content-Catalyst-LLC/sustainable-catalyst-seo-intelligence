"""Reconcile a bounded capture with offline sitemap and publication exports."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from bounded_public_crawl import PREFIX, Page, path_of, sha, timestamp

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
SLOW_IDS = {17, 19, 23, 24}


def xml(path, kind):
    with path.open("rb") as stream:
        data = stream.read(20_000_001)
    if len(data) > 20_000_000 or b"\0" in data or b"<!DOCTYPE" in data.upper():
        raise ValueError("Expected bounded UTF-8 XML without DTD")
    root = ET.fromstring(data.decode("utf-8-sig"))
    if root.tag != NS + kind:
        raise ValueError(f"Expected sitemap {kind}")
    return root, sha(data)


def sitemap_membership(index, export=None):
    root, digest = xml(index, "sitemapindex")
    declared = {}
    for node in root.findall(NS + "sitemap"):
        url = (node.findtext(NS + "loc") or "").strip()
        path = path_of(url)
        if not path or not path.endswith(".xml"):
            raise ValueError("Invalid or non-property sitemap index entry")
        filename = path.rsplit("/", 1)[-1]
        if filename in declared:
            raise ValueError("Sitemap basenames must identify distinct index children")
        declared[filename] = url
    if not declared:
        raise ValueError("Captured sitemap index is empty")
    loaded, missing, rejected = {}, [], {}
    members = defaultdict(list)
    for name, url in sorted(declared.items()):
        path = export / name if export is not None else None
        if path is None or not path.is_file():
            missing.append(url)
            continue
        try:
            leaf, leaf_digest = xml(path, "urlset")
            urls = []
            for entry in leaf.findall(NS + "url"):
                loc = entry.findtext(NS + "loc")
                if not loc or not urlsplit(loc).scheme or not urlsplit(loc).netloc:
                    raise ValueError("Malformed sitemap location")
                urls.append(loc)
        except (ValueError, OSError, ET.ParseError) as exc:
            rejected[url] = str(exc)
            continue
        for loc in sorted(set(urls)):
            members[loc].append(url)
        loaded[url] = leaf_digest
    return members, {"index_sha256": digest, "declared": len(declared), "loaded": loaded,
                     "missing": missing, "rejected": rejected, "complete": not (missing or rejected)}


def publication_lookup(path):
    if path is None:
        return {}, None
    data = path.read_bytes()
    rows = json.loads(data)
    if not isinstance(rows, list):
        raise ValueError("Expected an array of publication records")
    statuses = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid publication record")
        url, status = row.get("url"), row.get("status")
        if not isinstance(url, str) or path_of(url) is None or not isinstance(status, str) or not status.strip():
            raise ValueError("Invalid publication URL or status")
        if url in statuses and statuses[url] != status:
            raise ValueError("Conflicting offline publication statuses")
        statuses[url] = status
    return statuses, sha(data)


def reconcile(records, members, complete, publications):
    rows = []
    for r in records:
        url = r["url"]
        canonicals = r.get("canonicals", [])
        canonical = canonicals[0] if len(canonicals) == 1 else None
        found = members.get(url, [])
        canonical_found = members.get(canonical, []) if canonical else []
        rows.append({
            "request": r["request"], "url": url, "public_http_status_at_capture": r["status"],
            "wordpress_status_from_offline_export": publications.get(url),
            "sitemap_membership": "listed" if found else "absent" if complete else "unknown",
            "sitemap_sources": found, "canonical_declarations": canonicals,
            "canonical_status": "self" if canonical == url else "different" if canonical else "missing_or_multiple",
            "canonical_sitemap_membership": (
                ("listed" if canonical_found else "absent" if complete else "unknown") if canonical else None),
            "canonical_sitemap_sources": canonical_found,
            "body_sha256": r["body_sha256"], "headers_sha256": r["headers_sha256"],
        })
    return rows


def replay_timings(capture, manifest):
    bodies = {}
    guide_profiles = []
    for item in manifest["requests"]:
        if item["kind"] == "page" and item["path"].startswith(PREFIX) and item["status"] == 200:
            body = (capture / item["body"]).read_bytes()
            if sha(body) != item["body_sha256"]:
                raise ValueError("Timing capture body hash mismatch")
            inline_styles = re.findall(rb"<style\b[^>]*>.*?</style\s*>", body, flags=re.I | re.S)
            guide_profiles.append({"request": item["request"], "body_bytes": len(body),
                                   "inline_style_block_bytes": sum(map(len, inline_styles)),
                                   "inline_styles_sha256": sha(b"".join(inline_styles)),
                                   "original_public_total_seconds": item["response_seconds"]})
            if item["request"] in SLOW_IDS:
                bodies[item["path"]] = body

    class Replay(BaseHTTPRequestHandler):
        def do_GET(self):
            data = bodies[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    timings = []
    with HTTPServer(("127.0.0.1", 0), Replay) as server, ThreadPoolExecutor(1) as worker:
        worker.submit(server.serve_forever)
        try:
            for item in (r for r in manifest["requests"] if r["request"] in SLOW_IDS):
                samples = []
                for _ in range(5):
                    start = perf_counter()
                    http = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        http.request("GET", item["path"])
                        response = http.getresponse()
                        data = response.read()
                    finally:
                        http.close()
                    end = perf_counter()
                    if response.status != 200 or sha(data) != item["body_sha256"]:
                        raise ValueError("Loopback replay differs from capture")
                    Page(item["url"], data)
                    samples.append({"loopback_transfer_ms": round((end - start) * 1000, 3),
                                    "offline_html_parse_ms": round((perf_counter() - end) * 1000, 3)})
                timings.append({"request": item["request"], "url": item["url"], "body_bytes": len(data),
                                "original_public_total_seconds": item["response_seconds"], "local_repeats": samples,
                                "median_local_transfer_ms": median(r["loopback_transfer_ms"] for r in samples),
                                "median_local_parse_ms": median(r["offline_html_parse_ms"] for r in samples),
                                "body_sha256": item["body_sha256"]})
        finally:
            server.shutdown()
    return timings, guide_profiles


def main(capture, output, export=None, publications=None):
    started = timestamp()
    f = capture / "sc-seo-02-technical-findings.json"
    m = capture / "sc-seo-02-public-crawl-manifest.json"
    receipt = json.loads((capture / "sc-seo-02-acceptance-receipt.json").read_bytes())
    for path in (f, m):
        if sha(path.read_bytes()) != receipt["artifact_sha256"][path.name]:
            raise ValueError("Prior acceptance receipt hash mismatch")
    findings = json.loads(f.read_bytes())
    manifest = json.loads(m.read_bytes())
    index = next(r for r in manifest["requests"] if r["kind"] == "sitemap")
    index_path = capture / index["body"]
    members, sources = sitemap_membership(index_path, export)
    if sources["index_sha256"] != index["body_sha256"]:
        raise ValueError("Captured index hash mismatch")
    statuses, statuses_sha = publication_lookup(publications)
    rows = reconcile(findings["public_http"], members, sources["complete"], statuses)
    unavailable = "unavailable_pending_verification"
    input_availability = {
        "authorized_child_sitemap_archive": "available" if sources["complete"] else (
            "incomplete" if export is not None else unavailable),
        "authoritative_wordpress_status_export": "available" if publications is not None else unavailable,
        "saved_repeat_public_performance_measurements": unavailable,
    }
    timing, guide_profiles = replay_timings(capture, manifest)
    output.mkdir(mode=0o700, exist_ok=False)
    report = output / "sc-seo-02-1e-reconciliation.json"
    report.write_text(json.dumps({"sitemaps": sources, "pages": rows, "offline_timings": timing,
                                  "guide_payload_profiles": guide_profiles,
                                  "input_availability": input_availability,
                                  "public_repeat_performance_verified": False,
                                  "historic_google": {"matching_classifications":
                                      findings["historical_google"].get("matching_classifications", {})}},
                                 indent=2) + "\n")
    (output / "sc-seo-02-1e-acceptance-receipt.json").write_text(json.dumps({
        "started_at_utc": started, "finished_at_utc": timestamp(), "live_origin_requests": 0,
        "prior_manifest_sha256": sha(m.read_bytes()), "prior_findings_sha256": sha(f.read_bytes()),
        "report_sha256": sha(report.read_bytes()), "pages": len(rows), "sitemaps": sources,
        "publication_export_sha256": statuses_sha,
        "wordpress_status_verified_count": sum(bool(r["wordpress_status_from_offline_export"]) for r in rows),
        "self_canonicals": sum(r["canonical_status"] == "self" for r in rows),
        "offline_timing_sample_count": sum(len(r["local_repeats"]) for r in timing),
        "repeat_origin_timings_available": False,
        "input_availability": input_availability, "public_repeat_performance_verified": False,
    }, indent=2) + "\n")


if __name__ == "__main__":
    os.umask(0o077)
    cli = argparse.ArgumentParser()
    cli.add_argument("capture", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--child-sitemaps", type=Path)
    cli.add_argument("--publication-status", type=Path)
    args = cli.parse_args()
    main(args.capture, args.output, args.child_sitemaps, args.publication_status)
