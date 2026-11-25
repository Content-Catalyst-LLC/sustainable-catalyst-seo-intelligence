"""SC-SEO-STUDIO-EXPORT-01: Excel review workbook for one stored evidence run (read-only).

Built only from the append-only evidence store (``seo_store``) and its existing contracts: recommendation IDs from
``seo_recommendations``, observation IDs and source SHA-256 from ``seo_evidence``, the evidence classes measured /
prior / inferred, and the Studio's page filters. No separate reporting model and no network access.

Every sheet carries:

* a banner (row 1) stating that the run is a dated historical snapshot, never current site state;
* per-row *Evidence type* and *Observed* columns, so Search Console observations (historical, dated by their pull
  time and reporting range) are never confused with the run's HTTP crawl or sitemap snapshot;
* stable record IDs (recommendation IDs from the store; ``F-``/``U-``/``S-`` IDs derived deterministically from the
  stored row), the run ID and a *Row fingerprint* (SHA-256 of the original evidence cells) so a re-import can tell
  reviewer edits from edits to evidence;
* reviewer columns (highlighted) that are the only columns meant to be edited.

The export opens the store read-only and writes nothing anywhere; the workbook carries no session, signature or
credential (only the signed-in person's Workspace subject, for the audit trail).
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
import re

import seo_store
import seo_xlsx as x
from seo_studio import page_type

EXPORT_SCHEMA = "sc-seo-studio-export/1"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DECISIONS = ("Approve", "Reject", "Defer", "Needs information")
REVIEW_STATUSES = ("Not started", "In review", "Done")
REVIEW = [x.Column("Review decision", 16, review=True, choices=DECISIONS),
          x.Column("Review status", 14, review=True, choices=REVIEW_STATUSES),
          x.Column("Reviewer notes", 40, review=True), x.Column("Reviewed by", 16, review=True),
          x.Column("Reviewed on (YYYY-MM-DD)", 14, review=True)]
SOURCE_LABEL = {
    "http_crawl": "HTTP crawl (run snapshot)", "siteone": "HTTP crawl (run snapshot)",
    "historical_http": "HTTP status (historical record)", "sitemap": "Sitemap fetch (run snapshot)",
    "wordpress": "WordPress record (export)", "audit_finding": "Prior audit (imported)",
    "gsc_api": "Search Console API (historical observation)",
    "gsc_export": "Search Console export (historical observation)"}
SEARCH_CONSOLE = ("gsc_api", "gsc_export")
SCOPES = ("run", "filtered")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _sha(*parts):
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def fingerprint(values):
    """SHA-256 (first 16 hex) of a row's evidence cells as a spreadsheet reads them back: each cell as text, empty
    cells as "". A re-import recomputes it from the returned cells to detect edits outside the review columns."""
    def text(v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return x.clean(v)
    return hashlib.sha256("\x1f".join(text(v) for v in values).encode()).hexdigest()[:16]


def finding_category(action):
    a = action.lower()
    if a.startswith("repair broken link to ") or "broken internal link" in a:
        return "broken"
    if any(k in a for k in ("meta description", "title", "h1", "heading", "description tag")):
        return "metadata"
    if any(k in a for k in ("noindex", "canonical", "sitemap", "http 404", "failed http", "redirect", "fetch error",
                            "indexable")):
        return "indexing"
    return "other"


def _peek(iterable):
    """(has_rows, iterator over all rows)."""
    it = iter(iterable)
    try:
        first = next(it)
    except StopIteration:
        return False, iter(())
    return True, itertools.chain([first], it)


class Export:
    def __init__(self, db, run_id, scope="run", flt="", query="", kind="", filters=None, subject="", now=None):
        if scope not in SCOPES:
            raise ValueError("unknown scope")
        filters = filters or {}
        if flt and flt not in filters:
            raise ValueError("unknown filter")
        self.db, self.run_id, self.scope, self.subject = db, run_id, scope, subject
        self.flt, self.query, self.kind = (flt, query[:200], kind[:40]) if scope == "filtered" else ("", "", "")
        self.filter_sql = filters.get(self.flt, "")
        self.now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        run = db.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(run_id)
        self.run = dict(run)
        self.window = json.loads(run["observed_window"]) if run["observed_window"] else {}
        self.extensions = json.loads(run["extensions"])
        self.referenced = set()
        self.counts = {}
        self.omitted = []

    # --- scope -----------------------------------------------------------------------------------------
    def _page_where(self):
        where, args = ["run_id = ?"], [self.run_id]
        if self.query:
            where.append("url LIKE ?")
            args.append("%" + self.query.replace("%", "") + "%")
        if self.filter_sql:
            where.append(self.filter_sql)
        return " AND ".join(where), args

    @property
    def url_filtered(self):
        return bool(self.query or self.filter_sql)

    def scope_text(self):
        if self.scope == "run":
            return "Entire run"
        parts = [f"page filter = {self.flt}" if self.flt else "", f"URL contains “{self.query}”" if self.query else "",
                 f"recommendation kind = {self.kind}" if self.kind else ""]
        return "Current filters: " + ("; ".join(p for p in parts if p) or "none (same as entire run)")

    # --- labels ----------------------------------------------------------------------------------------
    def snapshot_observed(self):
        w = self.window
        if not w:
            return f"run imported {self.run['imported_at_utc'] or 'time not recorded'} (observation window not recorded)"
        start = w.get("start") or (f"{w['start_operator_reported']} (operator-reported)"
                                   if w.get("start_operator_reported") else "start not recorded")
        return (f"{start} → {w.get('end') or 'end not recorded'} "
                f"(run window, precision: {w.get('precision') or 'unknown'})")

    def observed(self, source_kind, source_time, facts=None):
        if source_kind in SEARCH_CONSOLE:
            rng = (facts or {}).get("range")
            span = f"; reporting range {rng[0]} to {rng[1]}" if isinstance(rng, list) and len(rng) == 2 else ""
            return f"pulled {source_time or 'time not recorded'}{span}"
        if source_kind == "wordpress" and source_time:
            return f"record time {source_time}"
        return self.snapshot_observed()

    def notice(self, extra=""):
        r = self.run
        return (f"HISTORICAL SNAPSHOT — not current site state. Run “{r['label']}” ({r['kind']}, {r['run_id']}); "
                f"evidence observed {self.snapshot_observed()}; imported {r['imported_at_utc'] or 'time not recorded'}. Search Console rows "
                f"(if any) are separately dated historical observations. {extra}Exported {self.iso()} · "
                f"{self.scope_text()} · Edit only the highlighted review columns.")

    def iso(self):
        return self.now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- data ------------------------------------------------------------------------------------------
    def load(self):
        db, run = self.db, self.run_id
        where, args = self._page_where()
        self.pages = [dict(r) for r in db.execute(f"SELECT * FROM page WHERE {where} ORDER BY url", args)]
        self.urls = {p["url"] for p in self.pages}
        self.crawl, self.wordpress, self.gsc = {}, {}, []
        for oid, url, kind, sha, rec, t, facts in db.execute(
                "SELECT observation_id, url, source_kind, source_sha256, source_record, source_time_raw, facts FROM "
                "observation WHERE run_id = ? AND source_kind IN ('http_crawl','siteone','wordpress','gsc_api',"
                "'gsc_export') ORDER BY url, source_kind, source_record", (run,)):
            if url not in self.urls:
                continue
            f = json.loads(facts)
            if kind in ("http_crawl", "siteone") and "broken_link" not in f:
                self.crawl[url] = (oid, kind, f)
            elif kind == "wordpress":
                self.wordpress[url] = (oid, t, f)
            elif kind in SEARCH_CONSOLE:
                self.gsc.append((oid, url, kind, t, f))
        self.findings = []
        seen = defaultdict(int)
        for url, prio, action, oid, cls, origin in db.execute(
                "SELECT url, priority, action, observation_id, evidence_class, origin FROM finding WHERE run_id = ? "
                "ORDER BY url, evidence_class, priority, action, observation_id", (run,)):
            if url not in self.urls:
                continue
            key = _sha(run, url, cls, prio, action, oid)
            seen[key] += 1
            fid = "F-" + key[:16] + (f"-{seen[key]}" if seen[key] > 1 else "")
            self.findings.append({"id": fid, "url": url, "priority": prio, "action": action, "observation_id": oid,
                                  "evidence_class": cls, "origin": origin, "category": finding_category(action)})
        ids = {f["observation_id"] for f in self.findings}
        self.obs_facts = {}
        for oid, kind, facts in db.execute(
                "SELECT observation_id, source_kind, facts FROM observation WHERE run_id = ? AND (source_kind = "
                "'audit_finding' OR facts LIKE '%\"broken_link\"%')", (run,)):
            if oid in ids:
                self.obs_facts[oid] = (kind, json.loads(facts))
        rec_where, rec_args = "run_id = ?" + (" AND kind = ?" if self.kind else ""), [run] + ([self.kind] if self.kind else [])
        self.recs = []
        for r in db.execute(f"SELECT * FROM recommendation WHERE {rec_where} ORDER BY kind, subject, recommendation_id",
                            rec_args):
            r = dict(r)
            if self.url_filtered and r["subject"] not in self.urls:
                continue
            r["basis"] = json.loads(r["basis"])
            self.recs.append(r)
        self.recs_by_subject = defaultdict(list)
        for r in self.recs:
            self.recs_by_subject[r["subject"]].append(r)
        self.worst = {}
        for f in self.findings:
            if f["evidence_class"] == "measured":
                cur = self.worst.get(f["url"])
                if cur is None or PRIORITY_ORDER.get(f["priority"], 9) < PRIORITY_ORDER.get(cur, 9):
                    self.worst[f["url"]] = f["priority"]
        return self

    # --- helpers -------------------------------------------------------------------------------------------
    def ref(self, *oids):
        for oid in oids:
            if oid:
                self.referenced.add(oid)
        return ", ".join(o for o in oids if o)

    def row(self, evidence, review=None):
        """Evidence values + fingerprint + review columns (empty, Review status 'Not started')."""
        return list(evidence) + [fingerprint(evidence)] + (review or ["", "Not started", "", "", ""])

    @staticmethod
    def basis_refs(basis):
        refs = []
        for key in ("observation", "wordpress_observation"):
            if isinstance(basis.get(key), str):
                refs.append(basis[key])
        for key in ("broken_link_observations", "wordpress_observations"):
            if isinstance(basis.get(key), list):
                refs.extend(v for v in basis[key] if isinstance(v, str))
        return refs

    def crawl_bits(self, url):
        oid, kind, f = self.crawl.get(url, (None, None, {}))
        h1 = f.get("h1")
        h1 = " | ".join(h1) if isinstance(h1, list) else (h1 or "")
        return oid, kind, f, h1

    # --- sheets --------------------------------------------------------------------------------------------
    TAIL = [x.Column("Run ID", 22), x.Column("Row fingerprint", 18)]

    def recommendations_rows(self):
        for r in self.recs:
            refs = self.basis_refs(r["basis"])
            kinds = sorted({k for k, _ in (self.source_of(o) for o in refs) if k})
            evidence = [r["recommendation_id"], r["kind"], r["subject"], r["summary"], r["evidence_class"],
                        r["approval"], r["status"], self.worst.get(r["subject"], ""), self.ref(*refs[:200]),
                        r["basis"].get("count", len(refs)), r["basis"].get("change_event") or "",
                        "Inferred recommendation" + (f" from {', '.join(SOURCE_LABEL.get(k, k) for k in kinds)}" if kinds else ""),
                        self.snapshot_observed(), self.run_id]
            yield self.row(evidence)

    def source_of(self, oid):
        if not hasattr(self, "_source"):
            self._source = {o: k for o, k in self.db.execute(
                "SELECT observation_id, source_kind FROM observation WHERE run_id = ?", (self.run_id,))}
        return self._source.get(oid), oid

    def url_rows(self):
        for p in self.pages:
            oid, kind, _, _ = self.crawl_bits(p["url"])
            gsc = [g for g in self.gsc if g[1] == p["url"]]
            evidence = ["U-" + _sha(self.run_id, p["url"])[:16], p["url"], page_type(p["url"]), p["http_status"],
                        p["redirect_hops"], {1: "yes", 0: "no"}.get(p["sitemap_member"], ""), p["wordpress_status"],
                        p["post_type"], p["verification_state"], {1: "yes", 0: "no"}.get(p["noindex"], ""),
                        {1: "yes", 0: "no"}.get(p["canonical_self"], ""), {1: "yes", 0: "no"}.get(p["has_description"], ""),
                        p["h1_count"], p["structured_data"], p["ttfb_ms"], p["measured_findings"], p["prior_findings"],
                        p["broken_links_out"], len(self.recs_by_subject.get(p["url"], [])),
                        p["google_coverage"] or "", p["search_impressions"], p["search_clicks"],
                        self.ref(oid, *(g[0] for g in gsc)),
                        SOURCE_LABEL.get(kind, "No crawl observation in this run") +
                        ("; Search Console (historical)" if gsc else ""),
                        self.observed(kind or "", None) + ("; Search Console " + "; ".join(
                            self.observed(g[2], g[3], g[4]) for g in gsc[:3]) if gsc else ""), self.run_id]
            yield self.row(evidence)

    def finding_detail(self, f):
        kind, facts = self.obs_facts.get(f["observation_id"], (None, {}))
        if kind == "audit_finding":
            return (f"{facts.get('category', '')}: {facts.get('evidence', '')}".strip(": "), facts.get("proposed_fix", ""),
                    SOURCE_LABEL["audit_finding"])
        if "broken_link" in facts:
            return (f"link to {facts['broken_link']} returned {facts.get('broken_link_status')}", "",
                    SOURCE_LABEL.get(kind, ""))
        _, ckind, cf, _ = self.crawl_bits(f["url"])
        a = f["action"].lower()
        bits = []
        if "canonical" in a:
            bits.append("declared canonical(s): " + ", ".join(cf.get("declared_canonicals") or []) or "none")
        if "noindex" in a:
            bits.append(f"robots meta: {cf.get('robots_meta')}; X-Robots-Tag: {cf.get('x_robots') or '—'}")
        if "http" in a or "redirect" in a:
            bits.append(f"HTTP {cf.get('http_status')}; final URL {cf.get('final_url') or '—'}; "
                        f"redirect statuses {cf.get('redirect_chain_statuses') or '—'}")
        if "sitemap" in a:
            bits.append("sitemap member")
        return "; ".join(b for b in bits if b), "", SOURCE_LABEL.get(ckind or "http_crawl", "")

    def finding_rows(self, category):
        for f in self.findings:
            if f["category"] != category:
                continue
            detail, fix, label = self.finding_detail(f)
            oid = f["observation_id"]
            base = [f["id"], f["url"], f["action"], f["priority"], f["evidence_class"], label, detail, fix,
                    f["origin"] or "", self.ref(oid)]
            if category == "metadata":
                _, _, cf, h1 = self.crawl_bits(f["url"])
                desc = cf.get("page_description")
                related = [r for r in self.recs_by_subject.get(f["url"], []) if r["kind"] in ("meta_description", "headings")]
                base += [cf.get("page_title", ""), cf.get("title_count"), "yes" if desc else ("no" if cf else ""),
                         len(desc) if isinstance(desc, str) else None, cf.get("description_count"), cf.get("h1_count"),
                         h1, ", ".join(r["recommendation_id"] for r in related)]
            if category == "broken":
                target = f["action"][len("Repair broken link to "):] if f["action"].startswith("Repair broken link to ") \
                    else f["url"]
                source = f["url"] if f["action"].startswith("Repair broken link to ") else ""
                _, facts = self.obs_facts.get(oid, (None, {}))
                related = [r for r in self.recs_by_subject.get(target, [])
                           if r["kind"] in ("redirect", "redirect_decision", "link_repair")]
                base = [f["id"], source, target, facts.get("broken_link_status", "404 (prior audit)"), f["priority"],
                        f["evidence_class"], label, f["action"], ", ".join(r["recommendation_id"] for r in related),
                        ", ".join(sorted({r["kind"] for r in related})), detail, f["origin"] or "", self.ref(oid)]
            yield self.row(base + [self.snapshot_observed(), self.run_id])
        if category == "indexing":
            for oid, url, kind, t, facts in self.gsc:
                issue = ("Search Console coverage: " + facts["coverage_state"] if facts.get("coverage_state") else
                         "Search Console export: " + facts["export_classification"] if facts.get("export_classification")
                         else "Search Console performance")
                detail = "; ".join(f"{k}: {facts[k]}" for k in ("verdict", "coverage_state", "google_canonical",
                                                                 "impressions", "clicks", "ctr", "position")
                                   if facts.get(k) is not None)
                evidence = ["S-" + _sha(self.run_id, oid)[:16], url, issue, "", "search-console",
                            SOURCE_LABEL[kind], detail, "", "", self.ref(oid), self.observed(kind, t, facts), self.run_id]
                yield self.row(evidence)

    def category_rows(self):
        for p in self.pages:
            if page_type(p["url"]) != "Category archive":
                continue
            oid, kind, cf, h1 = self.crawl_bits(p["url"])
            related = self.recs_by_subject.get(p["url"], [])
            evidence = ["U-" + _sha(self.run_id, p["url"])[:16], p["url"], p["http_status"],
                        {1: "yes", 0: "no"}.get(p["sitemap_member"], ""), cf.get("page_title", ""), p["h1_count"], h1,
                        {1: "yes", 0: "no"}.get(p["has_description"], ""), p["measured_findings"], p["prior_findings"],
                        p["broken_links_out"], ", ".join(r["recommendation_id"] for r in related),
                        ", ".join(sorted({r["kind"] for r in related})), self.worst.get(p["url"], ""), self.ref(oid),
                        SOURCE_LABEL.get(kind, "No crawl observation in this run"), self.snapshot_observed(), self.run_id]
            yield self.row(evidence)

    def evidence_rows(self):
        artifacts = {sha: (kind, name) for kind, sha, name in self.db.execute(
            "SELECT source_kind, source_sha256, source_file FROM artifact WHERE run_id = ? ORDER BY source_kind, "
            "source_file", (self.run_id,))}
        for sha, (kind, name) in sorted(artifacts.items(), key=lambda kv: (kv[1][0], kv[1][1] or "")):
            yield ["artifact", sha, kind, name or "", sha, "", "", "", SOURCE_LABEL.get(kind, kind),
                   "Search Console (historical)" if kind in SEARCH_CONSOLE else self.snapshot_observed(), self.run_id]
        wanted = self.referenced
        for oid, url, kind, sha, rec, t, facts in self.db.execute(
                "SELECT observation_id, url, source_kind, source_sha256, source_record, source_time_raw, facts FROM "
                "observation WHERE run_id = ? ORDER BY url, source_kind, source_record", (self.run_id,)):
            if oid in wanted:
                f = json.loads(facts) if kind in SEARCH_CONSOLE else None
                yield ["observation", oid, kind, (artifacts.get(sha) or (None, ""))[1] or "", sha, url, rec, t or "",
                       SOURCE_LABEL.get(kind, kind), self.observed(kind, t, f), self.run_id]

    def overview_rows(self):
        r, b = self.run, self.extensions.get("baseline") or {}
        rec = b.get("reconciliation") or []
        status = {str(k if k is not None else "unknown"): v for k, v in self.db.execute(
            "SELECT http_status, COUNT(*) FROM page WHERE run_id = ? GROUP BY http_status", (self.run_id,))}
        gsc_runs = self.db.execute("SELECT COUNT(*) FROM observation WHERE run_id = ? AND source_kind IN "
                                   "('gsc_api','gsc_export')", (self.run_id,)).fetchone()[0]
        rows = [
            ("Evidence currency", "HISTORICAL SNAPSHOT. This workbook describes the run below as it was observed; it is "
                                  "not current site state. Re-check live pages before acting."),
            ("Export schema", EXPORT_SCHEMA), ("Exported (UTC)", self.iso()), ("Scope", self.scope_text()),
            ("Exported by (Workspace subject)", self.subject or "—"),
            ("Run label", r["label"]), ("Run ID", r["run_id"]), ("Run kind", r["kind"]),
            ("Imported (UTC)", r["imported_at_utc"]), ("Observed window", self.snapshot_observed()),
            ("Observed window basis", self.window.get("basis", "—")), ("Evidence history SHA-256", r["history_sha256"]),
            ("Property", r["property_scope"]), ("Baseline ID", b.get("baseline_id") or "—"),
            ("Reconciliation", f"{sum(1 for c in rec if c.get('match'))}/{len(rec)} checks match" if rec else "—"),
            ("HTTP status (all pages in run)", ", ".join(f"{k}: {v}" for k, v in sorted(status.items()))),
            ("Search Console evidence in run", f"{gsc_runs} observations (historical; dated in each row)" if gsc_runs else
             "None. Indexing is not inferred from HTTP status or sitemap membership."),
        ]
        # The overview is streamed last (deferred), so every other sheet has its final row count by now.
        self.counts.update((s.name, s.count) for s in self._sheets[1:])
        rows += [(f"Rows: {name}", count) for name, count in self.counts.items()]
        rows += [(f"Omitted sheet: {name}", reason) for name, reason in self.omitted]
        rows += [
            ("Evidence classes", "measured = recomputed from this run's observations · prior = earlier audit "
                                 "classification, imported verbatim · inferred = recommendation awaiting human approval · "
                                 "search-console = Google observation (historical)."),
            ("Record IDs", "Recommendation IDs are the store's IDs. F- (finding), U- (URL) and S- (Search Console) IDs "
                           "are derived from the stored row and stay the same for this run."),
            ("Review columns", "Edit only the highlighted columns: Review decision (" + " / ".join(DECISIONS) +
             "), Review status (" + " / ".join(REVIEW_STATUSES) + "), Reviewer notes, Reviewed by, Reviewed on."),
            ("Row fingerprint", "SHA-256 of the row's original evidence cells. A future re-import uses it to tell review "
                                "edits from changes to evidence."),
            ("Effect", "Exporting changed nothing. No recommendation is applied to WordPress or any website by this "
                       "workbook; approved changes ship only as authorized remediation batches."),
        ]
        for k, v in rows:
            yield [k, "—" if v is None or v == "" else v]

    # --- assembly ------------------------------------------------------------------------------------------
    def sheets(self):
        T = self.TAIL
        OBS, RUN = x.Column("Observed", 34, "wrap"), x.Column("Run ID", 22)
        FP = x.Column("Row fingerprint", 18)
        spec = [
            ("Recommendations", [x.Column("Record ID", 22), x.Column("Kind", 16), x.Column("Subject", 48, "url"),
                                 x.Column("Proposed action", 60, "wrap"), x.Column("Evidence class", 10),
                                 x.Column("Approval", 10), x.Column("Store status", 11),
                                 x.Column("Highest measured finding priority on subject", 12),
                                 x.Column("Evidence references (observation IDs)", 40, "wrap"),
                                 x.Column("Evidence reference count", 10, "int"), x.Column("Change event", 10),
                                 x.Column("Evidence type", 30, "wrap"), OBS, RUN, FP],
             self.recommendations_rows(), True),
            ("URL Inventory", [x.Column("Record ID", 20), x.Column("URL", 60, "url"), x.Column("Page type", 16),
                               x.Column("HTTP status", 8, "int"), x.Column("Redirect hops", 8, "int"),
                               x.Column("In sitemap", 8), x.Column("WordPress status", 10), x.Column("Post type", 14),
                               x.Column("KB verification state", 14), x.Column("noindex", 8),
                               x.Column("Canonical is self", 9), x.Column("Has meta description", 10),
                               x.Column("H1 count", 7, "int"), x.Column("Structured data", 11),
                               x.Column("TTFB ms", 8, "int"), x.Column("Measured findings", 9, "int"),
                               x.Column("Prior findings", 9, "int"), x.Column("Broken links out", 9, "int"),
                               x.Column("Recommendations", 9, "int"),
                               x.Column("Search Console coverage (historical)", 18),
                               x.Column("Search impressions (historical)", 11, "int"),
                               x.Column("Search clicks (historical)", 9, "int"),
                               x.Column("Evidence references", 40, "wrap"), x.Column("Evidence type", 26, "wrap"),
                               OBS, RUN, FP], self.url_rows(), True),
        ]
        finding_cols = [x.Column("Record ID", 22), x.Column("URL", 56, "url"), x.Column("Issue / finding", 40, "wrap"),
                        x.Column("Priority", 7), x.Column("Evidence class", 13), x.Column("Evidence type", 24, "wrap"),
                        x.Column("Detail", 50, "wrap"), x.Column("Proposed fix (prior audit)", 30, "wrap"),
                        x.Column("Origin (prior analysis)", 24), x.Column("Evidence reference", 40, "wrap")]
        spec.append(("Indexing Issues", finding_cols + [OBS, RUN, FP], self.finding_rows("indexing"), False))
        spec.append(("Broken Links", [x.Column("Record ID", 22), x.Column("Source page", 50, "url"),
                                      x.Column("Broken target", 50, "url"), x.Column("Target status", 10),
                                      x.Column("Priority", 7), x.Column("Evidence class", 10),
                                      x.Column("Evidence type", 24, "wrap"), x.Column("Proposed action", 40, "wrap"),
                                      x.Column("Related recommendation IDs", 24, "wrap"),
                                      x.Column("Related recommendation kinds", 16), x.Column("Detail", 36, "wrap"),
                                      x.Column("Origin (prior analysis)", 24), x.Column("Evidence reference", 40, "wrap"),
                                      OBS, RUN, FP], self.finding_rows("broken"), False))
        spec.append(("Metadata & H1", finding_cols + [x.Column("Page title", 40, "wrap"), x.Column("Title count", 7, "int"),
                                                       x.Column("Has meta description", 10),
                                                       x.Column("Description length", 9, "int"),
                                                       x.Column("Description count", 9, "int"),
                                                       x.Column("H1 count", 7, "int"), x.Column("H1 text", 40, "wrap"),
                                                       x.Column("Related recommendation IDs", 24, "wrap"), OBS, RUN, FP],
                     self.finding_rows("metadata"), False))
        spec.append(("Category Archives", [x.Column("Record ID", 20), x.Column("URL", 56, "url"),
                                           x.Column("HTTP status", 8, "int"), x.Column("In sitemap", 8),
                                           x.Column("Page title", 40, "wrap"), x.Column("H1 count", 7, "int"),
                                           x.Column("H1 text", 40, "wrap"), x.Column("Has meta description", 10),
                                           x.Column("Measured findings", 9, "int"), x.Column("Prior findings", 9, "int"),
                                           x.Column("Broken links out", 9, "int"),
                                           x.Column("Related recommendation IDs", 24, "wrap"),
                                           x.Column("Related recommendation kinds", 16),
                                           x.Column("Highest measured finding priority", 10),
                                           x.Column("Evidence reference", 40, "wrap"), x.Column("Evidence type", 24, "wrap"),
                                           OBS, RUN, FP], self.category_rows(), False))
        spec.append(("Other Findings", finding_cols + [OBS, RUN, FP], self.finding_rows("other"), False))
        out = [x.Sheet("Overview", [x.Column("Item", 34), x.Column("Value", 110, "wrap")], self.overview_rows(),
                       self.notice())]
        for name, cols, rows, always in spec:
            has, rows = _peek(rows)
            if not has and not always:
                self.omitted.append((name, "no rows in this run/scope"))
                continue
            out.append(x.Sheet(name, cols + REVIEW, rows, self.notice()))
        out.append(x.Sheet("Evidence & Sources", [
            x.Column("Record type", 11), x.Column("ID", 66), x.Column("Source kind", 14), x.Column("Source file", 30),
            x.Column("Source SHA-256", 66), x.Column("URL", 50, "url"), x.Column("Source record #", 9, "int"),
            x.Column("Source record time", 22), x.Column("Evidence type", 28, "wrap"), OBS, RUN],
            self.evidence_rows(), self.notice("Lists every artifact of the run and every observation referenced by "
                                              "another sheet. ")))
        return out

    def build(self):
        self.load()
        sheets = self.sheets()
        buf = io.BytesIO()

        self._sheets = sheets
        x.write(buf, sheets, f"SEO Studio export — {self.run['label']}", self.iso(), deferred=(0,))
        return buf.getvalue()

    def filename(self):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", self.run_id)
        return f"seo-studio-{safe}-{'filtered' if self.scope == 'filtered' else 'run'}-{self.now:%Y%m%dT%H%M%SZ}.xlsx"


def readonly_connection(db):
    """A fresh read-only connection to the same SQLite file (exports never share or write the server's handle)."""
    path = next(row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main")
    conn = seo_store.connect(path, readonly=True)
    conn.execute("PRAGMA temp_store = MEMORY")  # sorts never need a writable temp directory (read-only container)
    return conn


def export_workbook(db, run_id, **kw):
    """(xlsx bytes, filename, row counts per sheet). Raises KeyError for an unknown run, ValueError for bad input."""
    conn = readonly_connection(db)
    try:
        e = Export(conn, run_id, **kw)
        data = e.build()
        return data, e.filename(), dict(e.counts)
    finally:
        conn.close()
