"""Import a prior sitewide audit (SC-SEO-01, 2026-11-23) into the evidence history as a versioned baseline.

This is an adapter, not a crawler or analyser: it maps already-collected artifacts onto the existing
``seo.evidence-history.v1`` model and reuses the existing sitemap, WordPress-status and reconciliation code.

* ``http_crawl`` observations: one per captured request (status, redirects, canonical, robots, metadata,
  headings, structured data re-validated offline by ``seo_structured_data``, timing and cache headers).
  The source rows carry no request time, so ``source_time_raw`` stays null; the run window is recorded once.
* ``broken_link`` relations: internal links from captured HTML pages to URLs the same crawl observed failing,
  using the original analyser's rule (fragment removed, status >= 400).
* ``audit_finding`` records: the original URL-level classifications, verbatim, with the analyser version.
* Sitemap leaves through ``EvidenceHistory.sitemap``; membership completeness through
  ``reconcile_sitemaps.sitemap_membership``; WordPress and Knowledge Base states through
  ``EvidenceHistory.publications`` (states copied, never upgraded).
* Release batches as change events, with the deployment state taken from a dated production observation.

Outputs stay in a private directory: the history JSON, a receipt (input and output SHA-256, row counts) and a
reconciliation table comparing imported counts with the original audit summary. Nothing is fetched.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse, urlunparse

from seo_evidence import EvidenceHistory
from seo_redirect_proposals import observations_from_history, read_proposals, summary as redirect_summary, verify
from seo_siteone import property_origin, url_key, utc_timestamp
from seo_structured_data import validate_blocks

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from reconcile_sitemaps import sitemap_membership  # noqa: E402  (existing pipeline, reused)

VERSION = "1.0.0"
MAX_LINE_BYTES = 8 * 1024 * 1024
ANALYSIS_TOOLS = ("crawl.py", "analyse.py", "links.py", "ldcheck.py", "check_redirects.py", "kb_tiers.py",
                  "metadata_source.py")
KB_FIELDS = {  # export column -> evidence fact (states copied verbatim)
    "type": "post_type", "_scfs_content_verification_state": "verification_state",
    "_scfs_editorial_state": "editorial_state", "_scfs_content_lifecycle": "lifecycle",
    "_scfs_human_review_required": "human_review_required", "_scfs_kb_verification": "kb_verification",
    "_sc_foundation_status": "foundation_status", "_sc_document_intelligence_public": "document_intelligence_public",
    "rank_math_robots": "robots",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_link(url):
    """The original analyser's link key: drop params and fragment, keep the query."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", p.query, ""))


def crawl_rows(path):
    with path.open("rb") as stream:
        for line in stream:
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("Crawl row exceeds size limit")
            if line.strip():
                yield json.loads(line)


def crawl_facts(row):
    if "error" in row:
        return {"http_status": None, "transport_error": str(row["error"])[:200]}
    chain = row.get("chain") or []
    robots = row.get("robots_all") or ([row["robots"]] if row.get("robots") else [])
    h1 = row.get("h1") or []
    return {
        "http_status": row.get("status"), "final_url": row.get("final_url"),
        "redirect_hops": max(0, len(chain) - 1),
        "redirect_chain_statuses": [hop.get("status") for hop in chain],
        "content_type": row.get("content_type"),
        "declared_canonicals": list(row.get("canonical") or []),
        "robots_meta": list(robots), "x_robots": row.get("x_robots"),
        "page_title": row.get("title"), "title_count": row.get("title_count"),
        "page_description": row.get("description"), "description_count": row.get("description_count"),
        "h1": h1[:5], "h1_count": len(h1),
        "word_count": row.get("word_count"), "main_content_hash": row.get("main_hash"),
        "internal_link_count": row.get("internal_link_count"),
        "structured_data": validate_blocks(row.get("ld_raw") or []),
        "ttfb_ms": round(row["ttfb"] * 1000) if isinstance(row.get("ttfb"), (int, float)) else None,
        "total_ms": round(row["total"] * 1000) if isinstance(row.get("total"), (int, float)) else None,
        "response_bytes": row.get("bytes"), "x_cache": row.get("x_cache"),
        "cache_control": row.get("cache_control"),
        "article_modified_raw": row.get("article_modified"),
    }


class BaselineImport:
    def __init__(self, scope, imported_at, methodology):
        self.history = EvidenceHistory(scope, imported_at)
        self.scope = scope
        self.methodology = methodology
        self.inputs = {}
        self.counts = Counter()
        self.reconciliation = []
        self.rejected_rows = []

    def accept(self, kind, url, ordinal):
        """Apply the evidence model's URL policy up front so every rejected source row is itemised."""
        if url_key(url, self.scope):
            return True
        self.rejected_rows.append({"source_kind": kind, "source_record": ordinal,
                                   "reason": "URL outside property policy (whitespace, control characters or host)",
                                   "url_repr": repr(url)[:200]})
        return False

    def record_input(self, name, path, rows=None):
        self.inputs[name] = {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size,
                             "rows": rows}
        return self.inputs[name]["sha256"]

    # --- crawl -------------------------------------------------------------------------------------------------
    def crawl(self, path):
        digest = self.record_input("crawl", path)
        self.history.artifacts[("http_crawl", digest)] = {
            "source_kind": "http_crawl", "source_file": path.name, "source_sha256": digest,
            "imported_at_utc": self.history.imported_at, "observed_window": self.methodology["observed_window"],
            "collection_policy": self.methodology["collection_policy"]}
        status_of, latest = {}, {}
        for ordinal, row in enumerate(crawl_rows(path)):
            latest[row["url"]] = (ordinal, row)  # the original analyser kept the last row per URL
            self.counts["crawl_rows_in_file"] += 1
        for url, (ordinal, row) in latest.items():
            status_of[url] = row.get("status")
            if row.get("final_url"):
                status_of.setdefault(row["final_url"], row.get("status"))
        html_pages = 0
        broken = defaultdict(set)
        for url, (ordinal, row) in latest.items():
            self.counts["crawl_requests"] += 1
            if not self.accept("http_crawl", url, ordinal):
                self.counts["crawl_rejected_status:" + str(row.get("status"))] += 1
                continue
            self.history.add("http_crawl", digest, ordinal, url, crawl_facts(row), None)
            self.counts["status:" + str(row.get("status"))] += 1
            if row.get("status") == 200 and "html" in (row.get("content_type") or "") and url == row.get("final_url"):
                html_pages += 1
                for link in row.get("links") or []:
                    target = norm_link(link["href"]) if link.get("internal") else None
                    if target and target != url and (status_of.get(target) or 0) >= 400:
                        broken[target].add(url)
        self.counts["html_200_final"] = html_pages
        for target, sources in sorted(broken.items()):
            for source in sorted(sources):
                identity = hashlib.sha256(f"http_crawl:{digest}:broken:{source}:{target}".encode()).hexdigest()
                self.history.add("http_crawl", digest, None, source,
                                 {"broken_link": target, "broken_link_status": status_of.get(target)}, None, identity)
                self.counts["broken_link_edges"] += 1
        self.counts["broken_link_targets"] = len(broken)
        self.inputs["crawl"]["rows"] = self.counts["crawl_rows_in_file"]

    # --- prior classifications --------------------------------------------------------------------------------
    def audit_findings(self, path, analysis):
        digest = self.record_input("audit_findings", path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for ordinal, row in enumerate(csv.DictReader(stream)):
                self.counts["audit_findings_in_file"] += 1
                if not self.accept("audit_finding", row["url"], ordinal):
                    self.counts["audit_rejected_priority:" + row["priority"]] += 1
                    continue
                self.history.add("audit_finding", digest, ordinal, row["url"], {
                    "audit_priority": row["priority"], "category": row["category"], "section": row["section"],
                    "defect": row["defect"], "evidence": (row.get("evidence") or "")[:300],
                    "proposed_fix": row.get("proposed_fix"), "analysis": analysis}, None)
                self.counts["audit_findings"] += 1
                self.counts["audit_priority:" + row["priority"]] += 1
        self.inputs["audit_findings"]["rows"] = self.counts["audit_findings_in_file"]
        self.history.artifacts[("audit_finding", digest)] = {
            "source_kind": "audit_finding", "source_file": path.name, "source_sha256": digest,
            "imported_at_utc": self.history.imported_at, "analysis": analysis}

    def issue_groups(self, path):
        self.record_input("issue_groups", path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            groups = [{k: row.get(k) for k in ("id", "priority", "category", "defect", "affected", "scope",
                                                 "status", "change_ref")} for row in csv.DictReader(stream)]
        self.inputs["issue_groups"]["rows"] = len(groups)
        self.history.extensions.setdefault("baseline", {})["issue_groups"] = groups

    # --- sitemaps and WordPress state (existing importers) -----------------------------------------------------
    def sitemaps(self, index, leaves_dir):
        self.record_input("sitemap_index", index)
        members, sources = sitemap_membership(index, leaves_dir)
        for url, digest in sorted(sources["loaded"].items()):
            leaf = leaves_dir / url.rsplit("/", 1)[-1]
            self.history.sitemap(leaf)
            self.counts["sitemap_leaves"] += 1
        self.counts["sitemap_urls"] = len(members)
        self.history.extensions.setdefault("baseline", {})["sitemaps"] = {
            "index_sha256": sources["index_sha256"], "declared": sources["declared"],
            "loaded": len(sources["loaded"]), "missing": sources["missing"], "rejected": sources["rejected"],
            "complete": sources["complete"], "unique_urls": len(members)}
        return members, sources["complete"]

    def wordpress_status(self, path):
        data = json.loads(path.read_bytes())
        rows = data["rows"] if isinstance(data, dict) else data
        self.record_input("wordpress_status", path, len(rows))
        public = [r for r in rows if r.get("url")]
        self.counts["wordpress_records"] = len(rows)
        self.counts["wordpress_without_public_url"] = len(rows) - len(public)
        self.history.publications(path)
        return {r["publication_id"]: r for r in rows}

    def kb_governance(self, path, wordpress_by_id):
        rows = json.loads(path.read_bytes())
        minimized = []
        for row in rows:
            publication_id = f"wp:post:{row['id']}"
            status = (wordpress_by_id.get(publication_id) or {}).get("status")
            if not status:
                self.counts["kb_governance_without_wordpress_status"] += 1
                continue  # never assume a publication state the WordPress export does not show
            item = {"publication_id": publication_id, "url": row.get("url") or "", "status": status}
            for source, target in KB_FIELDS.items():
                value = row.get(source)
                if value not in (None, "", []):
                    item[target] = value if isinstance(value, str) else json.dumps(value)
            minimized.append(item)
        out = self.private_dir / "kb-governance.minimized.json"
        out.write_text(json.dumps(minimized, indent=1) + "\n")
        self.record_input("kb_governance", path, len(rows))
        self.history.publications(out)
        states = Counter(item.get("verification_state", "unrecorded") for item in minimized)
        self.counts["kb_governance_records"] = len(minimized)
        self.history.extensions.setdefault("baseline", {})["kb_verification_states"] = dict(states)
        return minimized

    # --- recommendation inputs and change events ---------------------------------------------------------------
    def redirects(self, approved, held, existing_sources):
        built = self.history.build()
        observations = observations_from_history(built, self.scope)
        existing = [line.split("\t")[1] if "\t" in line else line.strip()
                    for line in existing_sources.read_text().splitlines() if line.strip()]
        result = {}
        for name, path in (("approved", approved), ("held_for_review", held)):
            self.record_input("redirects_" + name, path)
            rows = verify(read_proposals(path.read_text()), existing, observations, self.scope)
            result[name] = {"summary": redirect_summary(rows), "results": rows}
        self.record_input("redirect_existing_rules", existing_sources, len(existing))
        result["evidence"] = "baseline crawl observations (2026-11-23); a current collector run is required before apply"
        self.history.extensions.setdefault("baseline", {})["redirect_verification"] = result

    def release_state(self, path):
        self.record_input("release_state", path)
        state = json.loads(path.read_bytes())
        self.history.extensions["change_events"] = [
            {**batch, "deployment_observed_at_utc": state["observed_at_utc"],
             "deployment_observation": state["conclusion"], "source_sha256": self.inputs["release_state"]["sha256"]}
            for batch in state["batches"]]

    # --- reconciliation ----------------------------------------------------------------------------------------
    def reconcile(self, summary_path):
        self.record_input("original_summary", summary_path)
        original = json.loads(summary_path.read_bytes())
        built = self.history.build()
        crawl_status = Counter()
        for page in built["pages"]:
            for row in page["observations"]:
                if row["source_kind"] == "http_crawl" and "broken_link" not in row["facts"]:
                    crawl_status[str(row["facts"]["http_status"])] += 1
        for key, value in self.counts.items():  # rows refused by the URL policy are itemised, not dropped
            if key.startswith("crawl_rejected_status:"):
                crawl_status[key.split(":", 1)[1]] += value
        audit_priority = Counter({k.split(":", 1)[1]: v for k, v in self.counts.items() if k.startswith("audit_priority:")})
        for key, value in self.counts.items():
            if key.startswith("audit_rejected_priority:"):
                audit_priority[key.split(":", 1)[1]] += value
        expected_issue_priority = original["issues_by_priority"]
        checks = [
            ("crawled requests", original["crawled"], self.counts["crawl_requests"]),
            ("HTML 200 final pages", original["html_200_final"], self.counts["html_200_final"]),
            ("HTTP status distribution (accepted + itemised rejects)", dict(sorted(original["status_counts"].items())),
             dict(sorted(crawl_status.items()))),
            ("sitemap URLs (unique)", original["sitemap_urls"], self.counts["sitemap_urls"]),
            ("broken internal link targets", len(original["broken_targets"]), self.counts["broken_link_targets"]),
            ("broken internal link edges", sum(original["broken_targets"].values()), self.counts["broken_link_edges"]),
            ("URL-level findings (accepted + itemised rejects)", sum(expected_issue_priority.values()),
             self.counts["audit_findings"] + sum(v for k, v in self.counts.items() if k.startswith("audit_rejected"))),
            ("findings by priority", dict(sorted(expected_issue_priority.items())), dict(sorted(audit_priority.items()))),
            ("issue groups", 32, len(self.history.extensions.get("baseline", {}).get("issue_groups", []))),
        ]
        self.reconciliation = [{"check": name, "original": exp, "imported": got, "match": exp == got}
                               for name, exp, got in checks]
        return all(row["match"] for row in self.reconciliation)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cli.add_argument("--evidence-dir", required=True, type=Path, help="Private SC-SEO-01 evidence root")
    cli.add_argument("--release-dir", required=True, type=Path, help="Private SC-SEO-01 release kit")
    cli.add_argument("--tool-dir", required=True, type=Path, help="Frozen SC-SEO-01 analysers (hashed, not run)")
    cli.add_argument("--issue-groups", required=True, type=Path)
    cli.add_argument("--existing-redirects", type=Path,
                     help="Current redirect rules export (tab-separated, source path in column 2; default: "
                          "<release-dir>/plans/b3-existing-items.tsv)")
    cli.add_argument("--output-dir", required=True, type=Path)
    cli.add_argument("--property", default="https://sustainablecatalyst.com/")
    cli.add_argument("--imported-at", help="Fixed UTC import time for reproducible receipts")
    args = cli.parse_args(argv)
    ev, rel = args.evidence_dir, args.release_dir
    out = args.output_dir.expanduser().resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    methodology = json.loads((ev / "raw/baseline-methodology.json").read_bytes())
    scope = property_origin(args.property)
    job = BaselineImport(scope, args.imported_at or utc_timestamp(None), methodology)
    job.private_dir = out
    versions = {name: sha256_file(args.tool_dir / name) for name in ANALYSIS_TOOLS if (args.tool_dir / name).is_file()}
    analysis = "sc-seo-01/analyse.py@" + versions.get("analyse.py", "unknown")[:16]

    job.crawl(ev / "raw/crawl-full.jsonl")
    job.audit_findings(ev / "analysis/full-issues.csv", analysis)
    job.issue_groups(args.issue_groups)
    job.sitemaps(ev / "raw/sitemap_index.xml", ev / "raw/sm")
    wordpress = job.wordpress_status(ev / "raw/wp-status-20261124.json")
    job.kb_governance(rel / "plans/eligibility-raw.json", wordpress)
    job.redirects(rel / "redirects-approved.csv", rel / "redirects-held-for-review.csv",
                  args.existing_redirects or rel / "plans/b3-existing-items.tsv")
    job.release_state(ev / "raw/release-state-20261124.json")
    reconciled = job.reconcile(ev / "analysis/full-summary.json")

    job.history.extensions.setdefault("baseline", {}).update({
        "baseline_id": methodology["baseline_id"], "work_identifier": methodology["work_identifier"],
        "adapter": "seo_baseline/" + VERSION, "observed_window": methodology["observed_window"],
        "collection_policy": methodology["collection_policy"], "validation": methodology["validation"],
        "analysis_versions": versions, "reconciliation": job.reconciliation, "reconciled": reconciled,
        "rejected_rows": job.rejected_rows})
    history = job.history.build()
    history_path = out / "seo-baseline-history.json"
    history_path.write_text(json.dumps(history, ensure_ascii=False, sort_keys=True) + "\n")
    receipt = {
        "schema": "seo.baseline-import-receipt.v1", "adapter": "seo_baseline/" + VERSION,
        "baseline_id": methodology["baseline_id"], "imported_at_utc": job.history.imported_at,
        "inputs": job.inputs, "analysis_versions": versions, "counts": dict(sorted(job.counts.items())),
        "observations": sum(len(p["observations"]) for p in history["pages"]), "urls": len(history["pages"]),
        "rejected": history["rejected"], "rejected_rows": job.rejected_rows,
        "reconciliation": job.reconciliation, "reconciled": reconciled,
        "output_sha256": {history_path.name: sha256_file(history_path)}, "live_requests": 0,
    }
    (out / "seo-baseline-receipt.json").write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    for row in job.reconciliation:
        print(("MATCH  " if row["match"] else "DIFFER ") + row["check"] + f": original {row['original']} / imported {row['imported']}")
    print("receipt:", out / "seo-baseline-receipt.json")
    return 0 if reconciled else 2


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
