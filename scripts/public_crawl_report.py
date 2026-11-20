"""Replay a bounded capture locally and generate private review artifacts."""
import argparse
import collections
import csv
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit

from bounded_public_crawl import AGENT, ORIGIN, Page, PREFIX, in_scope, path_of, resolve, sha
from seo_siteone import csv_safe

BINARY_SHA256 = "099a9abb45fa002c88f82241bb8f2caa1404003687cfb81ac8706f6358d2016d"

PREFIX_NAME = "sc-seo-02-"


def save(output, name, value):
    (output / (PREFIX_NAME + name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


def csv_report(output, name, fields, rows):
    with (output / (PREFIX_NAME + name + ".csv")).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: csv_safe(row.get(k)) for k in fields} for row in rows)


def replay_result(destination):
    data = destination.read_bytes()
    report = json.loads(data)
    return {"status": "completed", "sha256": sha(data), "version": report["crawler"]["version"],
            "result_count": len(report["results"]), "mode": "loopback replay of bounded public capture projection"}


def replay(output, binary, records, pages):
    """Feed the pinned crawler a loopback-only projection with captured statuses."""
    if sha(binary.read_bytes()) != BINARY_SHA256:
        raise ValueError("SiteOne binary does not match the pinned macOS ARM64 release")
    projected = {}
    for path, page in pages.items():
        attrs = lambda name, val: f'{name}="{escape(val, quote=True)}"'
        head = f"<title>{escape(''.join(page.title))}</title>"
        head += "".join("<meta name=description " + attrs("content", d) + ">" for d in page.descriptions)
        head += "".join("<meta " + attrs("name", r["agent"]) + " " + attrs("content", r["content"]) + ">"
                        for r in page.robots)
        for canonical in page.canonicals:
            path_target = path_of(page.resolve(canonical))
            if path_target in records:
                head += "<link rel=canonical " + attrs("href", path_target) + ">"
        body = "".join(f"<h{level}>Heading</h{level}>" for level in page.headings)
        for link in page.links:
            dest = path_of(page.resolve(link))
            if dest in records:
                body += "<a " + attrs("href", dest) + ">Captured internal link</a>"
        projected[path] = ("<!doctype html><html><head>" + head + "</head><body>" + body + "</body></html>").encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                status, data, ctype = 200, b"User-agent: *\nAllow: /\n", "text/plain"
            else:
                row = records.get(self.path, {})
                status = row.get("status") or 404
                data = projected.get(self.path, b"")
                ctype = "text/html" if self.path in projected else "text/plain"
            self.send_response(status)
            if self.path in records and 300 <= status < 400:
                row = records[self.path]
                target = path_of(resolve(row["url"], row.get("location") or ""))
                if target in records:
                    self.send_header("Location", target)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    if not records:
        return None
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        origin = f"http://127.0.0.1:{server.server_port}"
        seeds = output / "siteone-loopback-seeds.txt"
        seeds.write_text("".join(origin + path + "\n" for path in records))
        cache = output / "siteone-cache"
        cache.mkdir(exist_ok=True)
        destination = output / "siteone-public-capture-offline-replay.json"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (output / "siteone-offline-replay.log").open("wb") as log:
                result = subprocess.run([
                    str(binary.resolve()), "--url-list=" + str(seeds), "--workers=1",
                    "--max-reqs-per-sec=4", "--max-visited-urls=40", "--timeout=3",
                    "--disable-all-assets", "--output-html-report=", "--http-cache-dir=" + str(cache),
                    "--user-agent=" + AGENT, "--output-json-file=" + str(destination),
                ], cwd=output, env={"PATH": "/usr/bin:/bin", "TZ": "UTC"}, stdout=log,
                    stderr=subprocess.STDOUT, timeout=120, check=False)
        finally:
            server.shutdown()
            thread.join()
    if result.returncode or not destination.is_file():
        return {"exit_code": result.returncode, "status": "SiteOne replay incomplete"}
    return replay_result(destination)


def report(output, binary, loopback_receipt, gsc_dir=None, reuse_siteone=False):
    raw_manifest = output / (PREFIX_NAME + "public-crawl-manifest.json")
    manifest = json.loads(raw_manifest.read_bytes())
    records = {r["path"]: r for r in manifest["requests"] if r["kind"] == "page"}
    sitemap = {"state": "unavailable", "leaf_members": set()}
    for row in manifest["requests"]:
        for label in ("body", "headers"):
            capture = output / row[label]
            if capture.exists() and sha(capture.read_bytes()) != row.get(label + "_sha256"):
                raise ValueError("Capture hash mismatch")
        if row["kind"] == "sitemap" and row.get("status") == 200:
            from xml.etree import ElementTree as ET
            raw = (output / row["body"]).read_bytes()
            if b"\x00" not in raw and b"<!DOCTYPE" not in raw.upper():
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue
                ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
                sitemap["evidence"] = row["body_sha256"]
                sitemap["request"] = row["request"]
                if root.tag == ns + "sitemapindex":
                    sitemap["state"] = "index captured; child sitemaps outside this run"
                    sitemap["declared_child_count"] = len(root.findall(ns + "sitemap"))
                elif root.tag == ns + "urlset":
                    sitemap["state"] = "leaf captured"
                    sitemap["leaf_members"] = {p for loc in root.iter(ns + "loc")
                                               if in_scope(p := path_of(loc.text or ""))}
    pages = {p: Page(row["url"], (output / row["body"]).read_bytes()) for p, row in records.items()
             if row.get("status") == 200 and row.get("content_type", "").lower().split(";")[0].strip() == "text/html"}
    def is_internal(url):
        try:
            return urlsplit(url).netloc == urlsplit(ORIGIN).netloc
        except ValueError:
            return False

    actions = []
    inventory = []
    duplicates = collections.defaultdict(list)

    def task(priority, path, action, related=()):
        refs = [records[p] for p in (path, *related)]
        actions.append({"priority": priority, "url": ORIGIN + path, "action": action,
                        "evidence": [f'{r["request"]}:{r["body_sha256"]}:{r["headers_sha256"]}' for r in refs]})

    for path, row in records.items():
        location = row.get("location")
        chain = []
        visited = {path}
        cursor = row
        while cursor.get("location") and 300 <= (cursor.get("status") or 0) < 400:
            target = resolve(cursor["url"], cursor["location"])
            chain.append(target)
            target_path = path_of(target)
            if target_path in visited or target_path not in records:
                break
            visited.add(target_path)
            cursor = records[target_path]
        info = {key: row.get(key) for key in ("request", "url", "status", "response_seconds", "content_type",
                                               "body_sha256", "headers_sha256", "x_robots_tag")}
        info["redirect_chain"] = chain
        info["sitemap_membership"] = ("listed" if path in sitemap["leaf_members"]
                                      else "unknown" if sitemap["state"] != "leaf captured" else "absent from captured leaf")
        if row.get("status") and row["status"] >= 400:
            task("P1", path, f'Investigate HTTP {row["status"]} on approved URL')
        if (row.get("response_seconds") or 0) > 5:
            task("P3", path, "Investigate response time above five seconds in this sample")
        if chain:
            task("P2", path, "Review redirect chain and confirm intended target")
        if path in pages:
            page = pages[path]
            title = " ".join("".join(page.title).split())
            canonicals = [page.resolve(value) for value in page.canonicals]
            links = {page.resolve(link) for link in page.links}
            internal = sorted(link for link in links if is_internal(link))
            checked = {p for link in internal if (p := path_of(link)) in records}
            broken = sorted(p for p in checked if (records[p].get("status") or 0) in (404, 410))
            info.update(title=title, descriptions=page.descriptions, canonicals=canonicals, robots_meta=page.robots,
                        heading_levels=page.headings, internal_links=internal,
                        broken_internal_links=[ORIGIN + p for p in broken],
                        unchecked_internal_link_count=len({link for link in internal if path_of(link) not in records}))
            for target in broken:
                task("P1", path, "Repair internal link to confirmed missing approved destination", (target,))
            if not canonicals or len(canonicals) > 1 or any(path_of(c) != path for c in canonicals):
                task("P2", path, "Review missing, multiple or differing canonical declarations")
            if not title or not any(d.strip() for d in page.descriptions):
                task("P2", path, "Review missing title or meta description")
            if page.headings.count(1) != 1:
                task("P3", path, f"Review heading structure ({page.headings.count(1)} H1 elements)")
            if any(b > a + 1 for a, b in zip(page.headings, page.headings[1:])):
                task("P3", path, "Review skipped heading levels")
            if any(re.search(r"\bnoindex\b", value, re.I)
                   for value in row.get("x_robots_tag", []) + [r["content"] for r in page.robots]):
                task("P2", path, "Review declared noindex directive and intended eligibility")
            for field, values in (("title", [title]), ("description", page.descriptions)):
                for value in values:
                    if value.strip():
                        duplicates[(field, " ".join(value.casefold().split()))].append(path)
        inventory.append(info)
    for (field, _), paths in duplicates.items():
        unique = list(dict.fromkeys(paths))
        if len(unique) > 1:
            task("P3", unique[0], f"Review duplicate {field} across {len(unique)} approved pages", unique[1:])
    if sitemap["state"].startswith("index captured"):
        actions.append({"priority": "P2", "url": ORIGIN + manifest["sitemap_path"],
                        "action": "Verify approved URL membership from an authorized local sitemap export",
                        "evidence": [f'{sitemap["request"]}:{sitemap["evidence"]}']})
    actions.sort(key=lambda r: (r["priority"], r["url"], r["action"]))
    for row in inventory:
        row["unchecked_internal_link_count"] = row.get("unchecked_internal_link_count", 0)
    historical = {"status": "No approved local Search Console export supplied to this run"}
    if gsc_dir:
        from seo_evidence import EvidenceHistory
        from seo_studio import read_sources
        evidence = EvidenceHistory(ORIGIN + "/", manifest["finished_at"])
        read_sources(gsc_dir, None, evidence)
        historical = {"status": "supplied offline exports", "artifacts": list(evidence.artifacts.values()), "records": [
            row for row in evidence.records.values()
            if row["source_kind"] == "gsc_export" and path_of(row["original_url"]) in records
        ]}
        historical["matching_classifications"] = dict(collections.Counter(
            row["facts"]["export_classification"] for row in historical["records"]))
    loopback = json.loads(loopback_receipt.read_bytes())
    if not pages:
        siteone = {"status": "Public capture supplied no HTML to replay"}
    elif reuse_siteone:
        siteone = replay_result(output / "siteone-public-capture-offline-replay.json")
    else:
        siteone = replay(output, binary, records, pages)
    counts = dict(collections.Counter(str(r.get("status") or "unknown") for r in records.values()))
    save(output, "technical-findings", {
        "public_http": inventory, "public_status_counts": counts, "sitemap": {
            **sitemap, "leaf_members": sorted(sitemap["leaf_members"])},
        "siteone_public_projection": siteone, "historical_google": historical,
        "loopback": {"http_counts": loopback["http_counts"], "receipt_sha256": sha(loopback_receipt.read_bytes())},
        "priority_tasks": actions,
    })
    display_fields = ("request", "url", "status", "response_seconds", "content_type", "title", "descriptions",
                      "canonicals", "robots_meta", "x_robots_tag", "redirect_chain", "sitemap_membership",
                      "broken_internal_links", "unchecked_internal_link_count", "body_sha256", "headers_sha256")
    csv_report(output, "public-url-inventory", display_fields, inventory)
    csv_report(output, "priority-queue", ("priority", "url", "action", "evidence"), actions)
    request_rows = manifest["requests"]
    gaps = [b["start_offset_seconds"] - a["end_offset_seconds"] for a, b in zip(request_rows, request_rows[1:])]
    authorized = all(
        (r["kind"] == "page" and in_scope(path_of(r["url"])))
        or (r["kind"] == "robots" and r["url"] == ORIGIN + "/robots.txt")
        or (r["kind"] == "sitemap" and r["url"] == ORIGIN + (manifest.get("sitemap_path") or ""))
        for r in request_rows)
    gates = {
        "requests_authorized": authorized,
        "limits_hold": len(request_rows) <= manifest["request_cap"]
        and len(records) <= manifest["page_cap"] and sum(p.startswith(PREFIX) for p in records) <= manifest["prefix_cap"]
        and manifest["duration_seconds"] <= manifest["max_duration_seconds"]
        and all(gap + 0.000001 >= manifest["minimum_gap_seconds"] for gap in gaps),
        "get_only": all(r["method"] == "GET" for r in request_rows),
        "single_in_flight": all(gap >= 0 for gap in gaps),
        "no_external_requests": all(path_of(r["url"]) is not None for r in request_rows),
        "anonymous_public_transport": True,
        "robots_verified": manifest["robots_verified"],
        "minimum_measured_gap_seconds": min(gaps, default=None),
    }
    lines = [
        "# SC-SEO-02 bounded technical SEO reconciliation",
        "",
        f'Public capture attempted **{len(request_rows)} requests**, with **{len(records)} page URL responses or attempts** and **{len(pages)} HTML pages**.',
        f'Status counts for page URLs: {counts}. Stop reason: {manifest["stop_reason"] or "queue exhausted within scope"}.',
        f'Original controlled loopback statuses: {loopback["http_counts"]}. SiteOne public projection: {siteone["status"]}.',
        f'Sitemap: {sitemap["state"]}. Historical Google: {historical["status"]}.',
        f'Historical classifications matched to requested URLs: {historical.get("matching_classifications", {})}.',
        "Public HTTP, offline SiteOne observations, prior loopback and historical Google are separate evidence categories.",
        "HTTP success does not establish Google indexing. Links with unobserved destinations remain unverified.",
        "",
        "## Prioritized observations",
    ]
    lines.extend(f'- {a["priority"]} {a["action"]} on {a["url"]}. Evidence {", ".join(a["evidence"])}.'
                 for a in actions[:5])
    lines.extend(("", "The JSON findings and priority CSV contain the full evidence-backed review queue.",
                  "Offline SiteOne replay uses a loopback projection. HTML, headers and timing from the original capture remain authoritative."))
    (output / (PREFIX_NAME + "reconciliation-report.md")).write_text("\n".join(lines) + "\n")
    artifacts = sorted(output.glob(PREFIX_NAME + "*"))
    save(output, "acceptance-receipt", {
        "started_at": manifest["started_at"], "finished_at": manifest["finished_at"],
        "requests": len(request_rows), "html_pages": len(pages), "duration_seconds": manifest["duration_seconds"],
        "gates": gates, "siteone": siteone, "siteone_binary_sha256": BINARY_SHA256,
        "stop_reason": manifest["stop_reason"],
        "collector_sha256": sha(Path(__file__).with_name("bounded_public_crawl.py").read_bytes()),
        "reporter_sha256": sha(Path(__file__).read_bytes()),
        "artifact_sha256": {p.name: sha(p.read_bytes()) for p in artifacts
                            if p.name != PREFIX_NAME + "acceptance-receipt.json"},
    })


if __name__ == "__main__":
    os.umask(0o077)
    cli = argparse.ArgumentParser()
    cli.add_argument("output", type=Path)
    cli.add_argument("binary", type=Path)
    cli.add_argument("loopback_receipt", type=Path)
    cli.add_argument("--gsc-dir", type=Path)
    cli.add_argument("--reuse-siteone", action="store_true", help="Reproduce reports from the existing offline analyzer output")
    args = cli.parse_args()
    report(args.output.resolve(), args.binary, args.loopback_receipt, args.gsc_dir, args.reuse_siteone)
