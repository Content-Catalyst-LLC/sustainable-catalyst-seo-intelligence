"""Persistent, append-only evidence history for SEO Intelligence.

Development uses SQLite (standard library). Staging and production use PostgreSQL with the equivalent DDL in
``contracts/sql/seo-store.postgres.sql`` (separate database, writer and read-only roles, UPDATE/DELETE revoked).

A *run* is one imported ``seo.evidence-history.v1`` document (a baseline import, a Studio report, a collector run or
a connector pull). Rows are never updated or deleted: a correction is a new run. Findings keep their evidence class:

* ``measured``      — recomputed by the evidence rules from observations in the run;
* ``prior``         — a prior audit's classification, imported verbatim;
* recommendations   — ``inferred`` opportunities (``seo_recommendations``), always awaiting human approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from seo_siteone import url_key

SCHEMA_VERSION = 1
TABLES = ("run", "artifact", "observation", "page", "finding", "change_event", "recommendation",
          "search_performance")

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run (
  run_id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL, imported_at_utc TEXT NOT NULL,
  observed_window TEXT, history_sha256 TEXT NOT NULL UNIQUE, property_scope TEXT NOT NULL,
  receipt TEXT, extensions TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifact (
  run_id TEXT NOT NULL REFERENCES run(run_id), source_kind TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  source_file TEXT, details TEXT NOT NULL, PRIMARY KEY (run_id, source_kind, source_sha256));
CREATE TABLE IF NOT EXISTS observation (
  run_id TEXT NOT NULL REFERENCES run(run_id), observation_id TEXT NOT NULL, url TEXT NOT NULL,
  source_kind TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_record INTEGER, source_time_raw TEXT,
  facts TEXT NOT NULL, PRIMARY KEY (run_id, observation_id));
CREATE INDEX IF NOT EXISTS observation_url ON observation(run_id, url);
CREATE TABLE IF NOT EXISTS page (
  run_id TEXT NOT NULL REFERENCES run(run_id), url TEXT NOT NULL, http_status INTEGER, redirect_hops INTEGER,
  sitemap_member INTEGER, wordpress_status TEXT, post_type TEXT, verification_state TEXT, noindex INTEGER,
  canonical_self INTEGER, has_description INTEGER, h1_count INTEGER, structured_data TEXT, ttfb_ms INTEGER,
  x_cache TEXT, measured_findings INTEGER NOT NULL, prior_findings INTEGER NOT NULL, broken_links_out INTEGER NOT NULL,
  search_impressions INTEGER, search_clicks INTEGER, google_coverage TEXT,
  PRIMARY KEY (run_id, url));
CREATE TABLE IF NOT EXISTS finding (
  run_id TEXT NOT NULL REFERENCES run(run_id), url TEXT NOT NULL, priority TEXT NOT NULL, action TEXT NOT NULL,
  observation_id TEXT NOT NULL, evidence_class TEXT NOT NULL CHECK (evidence_class IN ('measured','prior')),
  origin TEXT);
CREATE INDEX IF NOT EXISTS finding_run ON finding(run_id, evidence_class, priority);
CREATE TABLE IF NOT EXISTS change_event (
  run_id TEXT NOT NULL REFERENCES run(run_id), batch TEXT NOT NULL, state TEXT NOT NULL, details TEXT NOT NULL,
  PRIMARY KEY (run_id, batch));
CREATE TABLE IF NOT EXISTS recommendation (
  run_id TEXT NOT NULL REFERENCES run(run_id), recommendation_id TEXT NOT NULL, kind TEXT NOT NULL,
  subject TEXT NOT NULL, summary TEXT NOT NULL, evidence_class TEXT NOT NULL CHECK (evidence_class = 'inferred'),
  basis TEXT NOT NULL, approval TEXT NOT NULL CHECK (approval = 'required'), status TEXT NOT NULL,
  PRIMARY KEY (run_id, recommendation_id));
CREATE TABLE IF NOT EXISTS search_performance (
  run_id TEXT NOT NULL REFERENCES run(run_id), engine TEXT NOT NULL, url TEXT NOT NULL, start_date TEXT NOT NULL,
  end_date TEXT NOT NULL, clicks INTEGER, impressions INTEGER, ctr REAL, position REAL,
  PRIMARY KEY (run_id, engine, url, start_date, end_date));
"""


def _append_only_triggers():
    out = []
    for table in TABLES + ("meta",):
        for op in ("UPDATE", "DELETE"):
            out.append(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                       f"BEGIN SELECT RAISE(ABORT, 'append-only evidence store: {op} on {table} refused'); END;")
    return "\n".join(out)


def connect(path, readonly=False):
    if readonly:
        db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, check_same_thread=False)
    else:
        db = sqlite3.connect(str(path))
        db.executescript(DDL + _append_only_triggers())
        db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        db.commit()
    db.row_factory = sqlite3.Row
    return db


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _page_summary(page, scope):
    """One row per URL for fast dashboard queries; every value is derived from the run's own observations."""
    summary = {"http_status": None, "redirect_hops": None, "wordpress_status": None, "post_type": None,
               "verification_state": None, "noindex": None, "canonical_self": None, "has_description": None,
               "h1_count": None, "structured_data": None, "ttfb_ms": None, "x_cache": None,
               "search_impressions": None, "search_clicks": None, "google_coverage": None}
    broken = 0
    for row in page["observations"]:
        facts, kind = row["facts"], row["source_kind"]
        if "broken_link" in facts:
            broken += 1
            continue
        if kind in ("http_crawl", "siteone", "historical_http") and facts.get("http_status") is not None:
            summary["http_status"] = facts["http_status"]
        if kind == "http_crawl" and facts.get("http_status") == 200:
            canon = facts.get("declared_canonicals") or []
            sd = facts.get("structured_data") or {}
            # Same rules as EvidenceHistory.build, so dashboard counts equal the measured findings.
            noindex = any("noindex" in d.lower().replace(",", " ").split()
                          for d in (facts.get("robots_meta") or []) + [facts.get("x_robots") or ""])
            summary.update(
                redirect_hops=facts.get("redirect_hops"), noindex=int(noindex),
                canonical_self=int(len(canon) == 1 and url_key(canon[0], scope) == page["url"]),
                has_description=int(bool(facts.get("page_description"))), h1_count=facts.get("h1_count"),
                structured_data=("none" if sd.get("blocks") == 0 else
                                 "invalid" if sd.get("parse", "ok") != "ok" else
                                 "unresolved" if sd.get("unresolved") else "ok") if sd else None,
                ttfb_ms=facts.get("ttfb_ms"), x_cache=facts.get("x_cache"))
        if kind == "wordpress":
            summary["wordpress_status"] = facts.get("status") or summary["wordpress_status"]
            summary["post_type"] = facts.get("post_type") or summary["post_type"]
            summary["verification_state"] = facts.get("verification_state") or summary["verification_state"]
        if kind == "gsc_api":
            if "impressions" in facts:
                summary["search_impressions"] = facts["impressions"]
                summary["search_clicks"] = facts.get("clicks")
            if facts.get("coverage_state"):
                summary["google_coverage"] = facts["coverage_state"]
        if kind == "gsc_export" and facts.get("export_classification"):
            summary["google_coverage"] = summary["google_coverage"] or "export:" + facts["export_classification"]
    measured = sum(1 for f in page["findings"] if "origin" not in f)
    return summary, measured, len(page["findings"]) - measured, broken


def import_history(db, history, label, kind, receipt=None, run_id=None):
    """Append one evidence history as a run. Refuses a history already imported (same SHA-256)."""
    raw = _dumps(history).encode()
    digest = hashlib.sha256(raw).hexdigest()
    if db.execute("SELECT 1 FROM run WHERE history_sha256 = ?", (digest,)).fetchone():
        raise ValueError("This evidence history is already stored (same SHA-256); runs are immutable")
    run_id = run_id or "run_" + digest[:16]
    extensions = {k: v for k, v in history.items() if k not in ("pages", "artifacts", "rejected", "schema_version",
                                                                  "property_scope")}
    window = (history.get("baseline") or {}).get("observed_window")
    with db:
        db.execute("INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?)", (
            run_id, label, kind, receipt.get("imported_at_utc") if receipt else history.get("imported_at_utc", ""),
            _dumps(window) if window else None, digest, history["property_scope"],
            _dumps(receipt) if receipt else None, _dumps(extensions)))
        for item in history["artifacts"]:
            db.execute("INSERT OR IGNORE INTO artifact VALUES (?,?,?,?,?)", (
                run_id, item["source_kind"], item["source_sha256"], item.get("source_file"), _dumps(item)))
        for page in history["pages"]:
            for row in page["observations"]:
                db.execute("INSERT INTO observation VALUES (?,?,?,?,?,?,?,?)", (
                    run_id, row["observation_id"], page["url"], row["source_kind"], row["source_sha256"],
                    row.get("source_record"), row.get("source_time_raw"), _dumps(row["facts"])))
            for finding in page["findings"]:
                db.execute("INSERT INTO finding VALUES (?,?,?,?,?,?,?)", (
                    run_id, page["url"], finding["priority"], finding["action"], finding["observation_id"],
                    "prior" if "origin" in finding else "measured", finding.get("origin")))
            summary, measured, prior, broken = _page_summary(page, history["property_scope"])
            db.execute("INSERT INTO page VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, page["url"], summary["http_status"], summary["redirect_hops"],
                None if page.get("sitemap_member") is None else int(page["sitemap_member"]),
                summary["wordpress_status"], summary["post_type"], summary["verification_state"],
                summary["noindex"], summary["canonical_self"], summary["has_description"], summary["h1_count"],
                summary["structured_data"], summary["ttfb_ms"], summary["x_cache"], measured, prior, broken,
                summary["search_impressions"], summary["search_clicks"], summary["google_coverage"]))
        for event in history.get("change_events") or []:
            db.execute("INSERT INTO change_event VALUES (?,?,?,?)", (run_id, event["batch"], event["state"], _dumps(event)))
        for row in history.get("search_performance") or []:
            db.execute("INSERT INTO search_performance VALUES (?,?,?,?,?,?,?,?,?)", (
                run_id, row["engine"], row["url"], row["start_date"], row["end_date"], row.get("clicks"),
                row.get("impressions"), row.get("ctr"), row.get("position")))
    return run_id


def add_recommendations(db, run_id, recommendations):
    with db:
        for rec in recommendations:
            db.execute("INSERT INTO recommendation VALUES (?,?,?,?,?,?,?,?,?)", (
                run_id, rec["recommendation_id"], rec["kind"], rec["subject"], rec["summary"], "inferred",
                _dumps(rec["basis"]), "required", "proposed"))


def runs(db):
    return [dict(r) for r in db.execute(
        "SELECT run_id, label, kind, imported_at_utc, observed_window, history_sha256 FROM run ORDER BY imported_at_utc, run_id")]


def compare(db, before, after):
    """Run-to-run comparison over URLs observed in both runs (absent observations stay unknown, not 'fixed')."""
    q = """SELECT a.url, a.http_status AS before_status, b.http_status AS after_status,
                  a.measured_findings AS before_findings, b.measured_findings AS after_findings
           FROM page a JOIN page b ON a.url = b.url AND a.run_id = ? AND b.run_id = ?
           WHERE a.http_status IS NOT NULL AND b.http_status IS NOT NULL"""
    rows = [dict(r) for r in db.execute(q, (before, after))]
    changed = [r for r in rows if r["before_status"] != r["after_status"]]
    only = lambda x, y: db.execute(  # noqa: E731
        "SELECT COUNT(*) FROM page WHERE run_id = ? AND http_status IS NOT NULL AND url NOT IN "
        "(SELECT url FROM page WHERE run_id = ? AND http_status IS NOT NULL)", (x, y)).fetchone()[0]
    return {"before": before, "after": after, "urls_observed_in_both": len(rows),
            "status_changed": changed[:500], "status_changed_count": len(changed),
            "only_in_before": only(before, after), "only_in_after": only(after, before),
            "note": "Only URLs observed in both runs are compared; a URL absent from one run is unknown there."}


def main(argv=None):
    cli = argparse.ArgumentParser(description="Append an evidence history to the SEO Intelligence store")
    cli.add_argument("--db", required=True, type=Path)
    cli.add_argument("--history", required=True, type=Path, help="seo-baseline-history.json or seo-summary.json")
    cli.add_argument("--receipt", type=Path)
    cli.add_argument("--label", required=True)
    cli.add_argument("--kind", required=True, choices=("baseline", "studio", "collector", "connector", "verification"))
    cli.add_argument("--with-recommendations", action="store_true",
                     help="Also store inferred recommendations (seo_recommendations) for this run")
    args = cli.parse_args(argv)
    data = json.loads(args.history.read_bytes())
    history = data.get("evidence_history", data)
    receipt = json.loads(args.receipt.read_bytes()) if args.receipt else None
    if receipt is None and "evidence_history" in data:
        receipt = {"imported_at_utc": data.get("generated_at"), "sources": data.get("sources")}
    db = connect(args.db)
    try:
        run_id = import_history(db, history, args.label, args.kind, receipt)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.with_recommendations:
        from seo_recommendations import derive
        add_recommendations(db, run_id, derive(history))
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE run_id = ?", (run_id,)).fetchone()[0] for t in TABLES[1:]}
    print(run_id, json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
