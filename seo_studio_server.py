"""SEO Studio: authenticated, read-only dashboard over the persistent evidence store.

* Identity: Workspace-signed ``sc-workspace-seo-session/1.0`` documents (``seo_workspace_auth``); every ``/api``
  route requires one and the subject must be on the Studio allowlist. The page shell and static assets hold no data.
* Data: the append-only store (``seo_store``), opened read-only. The server never writes and never fetches sites.
* Evidence classes stay visible: measured observations and findings, prior audit classifications, and inferred
  recommendations are separate API fields and separate UI sections.

Development: ``python3 seo_studio_server.py --db store.sqlite --keyring keyring.json --allow workspace-user:owner
--workspace-session-url http://127.0.0.1:18990/v1/seo-access/session``. Binds to loopback unless told otherwise;
production sits behind the existing reverse proxy and TLS.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from statistics import median
import threading
from urllib.parse import parse_qs, urlsplit

import seo_store
import seo_studio_export
from seo_workspace_auth import Keyring, SessionRefused, verify_session

STATIC = Path(__file__).resolve().parent / "studio"
PAGE_SIZE = 50


def _rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


def _counts(db, sql, args=()):
    return {str(k if k is not None else "unknown"): v for k, v in db.execute(sql, args)}


def _pct(values, q):
    values = sorted(v for v in values if v is not None)
    return values[min(len(values) - 1, int(q * (len(values) - 1)))] if values else None


def overview(db, run_id):
    run = db.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(run_id)
    ext = json.loads(run["extensions"])
    baseline = ext.get("baseline") or {}
    ttfb = [r[0] for r in db.execute("SELECT ttfb_ms FROM page WHERE run_id = ? AND http_status = 200", (run_id,))]
    has = lambda sql: db.execute(sql, (run_id,)).fetchone()[0]  # noqa: E731
    gsc_pages = has("SELECT COUNT(*) FROM page WHERE run_id = ? AND google_coverage IS NOT NULL")
    perf = db.execute("SELECT engine, COUNT(*), SUM(clicks), SUM(impressions) FROM search_performance WHERE run_id = ? "
                      "GROUP BY engine", (run_id,)).fetchall()
    return {
        "run": {k: run[k] for k in ("run_id", "label", "kind", "imported_at_utc", "history_sha256", "property_scope")},
        "observed_window": json.loads(run["observed_window"]) if run["observed_window"] else None,
        "provenance": {
            "artifacts": _rows(db, "SELECT source_kind, source_file, source_sha256 FROM artifact WHERE run_id = ? "
                                   "ORDER BY source_kind", (run_id,)),
            "collection_policy": baseline.get("collection_policy"), "validation": baseline.get("validation"),
            "analysis_versions": baseline.get("analysis_versions"), "reconciliation": baseline.get("reconciliation"),
            "rejected_rows": baseline.get("rejected_rows"), "baseline_id": baseline.get("baseline_id")},
        "technical": {  # measured
            "status": _counts(db, "SELECT http_status, COUNT(*) FROM page WHERE run_id = ? AND url IN (SELECT url FROM "
                                  "observation WHERE run_id = ? AND source_kind IN ('http_crawl','siteone')) "
                                  "GROUP BY http_status", (run_id, run_id)),
            "redirected": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND redirect_hops > 0"),
            "canonical_not_self": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND canonical_self = 0"),
            "noindex": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND noindex = 1"),
            "ttfb_ms": {"median": median(ttfb) if [t for t in ttfb if t is not None] else None,
                        "p95": _pct(ttfb, .95), "max": _pct(ttfb, 1.0)} if ttfb else None,
            "cache": _counts(db, "SELECT x_cache, COUNT(*) FROM page WHERE run_id = ? AND http_status = 200 GROUP BY x_cache",
                             (run_id,)),
            "measured_findings": _rows(db, "SELECT priority, action, COUNT(*) AS n FROM finding WHERE run_id = ? AND "
                                           "evidence_class = 'measured' AND action NOT LIKE 'Repair broken link to %' "
                                           "GROUP BY priority, action ORDER BY priority, n DESC", (run_id,))},
        "metadata": {  # measured + prior classifications, labelled
            "html_200_without_description": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND http_status = 200 "
                                                "AND has_description = 0"),
            "multiple_h1": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND h1_count > 1"),
            "no_h1": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND h1_count = 0"),
            "prior_classifications": _rows(db, "SELECT priority, action, COUNT(*) AS n FROM finding WHERE run_id = ? "
                                               "AND evidence_class = 'prior' GROUP BY priority, action "
                                               "ORDER BY n DESC LIMIT 25", (run_id,))},
        "broken_links": {  # measured
            "edges": has("SELECT COUNT(*) FROM finding WHERE run_id = ? AND action LIKE 'Repair broken link to %'"),
            "top_targets": _rows(db, "SELECT substr(action, 23) AS target, COUNT(*) AS inlinks FROM finding WHERE "
                                     "run_id = ? AND action LIKE 'Repair broken link to %' GROUP BY target "
                                     "ORDER BY inlinks DESC, target LIMIT 25", (run_id,))},
        "sitemaps": {  # measured membership vs WordPress state
            "summary": baseline.get("sitemaps"),
            "members_not_200": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND sitemap_member = 1 AND "
                                   "http_status IS NOT NULL AND http_status <> 200"),
            "members_noindex": has("SELECT COUNT(*) FROM page WHERE run_id = ? AND sitemap_member = 1 AND noindex = 1"),
            "published_not_in_sitemap": _counts(db, "SELECT post_type, COUNT(*) FROM page WHERE run_id = ? AND "
                                                    "wordpress_status = 'publish' AND sitemap_member IS NULL "
                                                    "GROUP BY post_type", (run_id,))},
        "structured_data": _counts(db, "SELECT structured_data, COUNT(*) FROM page WHERE run_id = ? AND "
                                       "structured_data IS NOT NULL GROUP BY structured_data", (run_id,)),
        "knowledge_base": {  # recorded states, never upgraded
            "verification_states": _counts(db, "SELECT verification_state, COUNT(*) FROM page WHERE run_id = ? AND "
                                               "post_type = 'sc_support_article' GROUP BY verification_state", (run_id,)),
            "in_sitemap_by_state": _counts(db, "SELECT verification_state, COUNT(*) FROM page WHERE run_id = ? AND "
                                               "post_type = 'sc_support_article' AND sitemap_member = 1 "
                                               "GROUP BY verification_state", (run_id,)),
            "note": "States are copied from the Knowledge Base; SEO Studio does not verify or approve articles."},
        "indexing": {  # search-engine evidence only
            "pages_with_google_evidence": gsc_pages,
            "coverage": _counts(db, "SELECT google_coverage, COUNT(*) FROM page WHERE run_id = ? AND google_coverage "
                                    "IS NOT NULL GROUP BY google_coverage", (run_id,)),
            "search_performance": [{"engine": e, "pages": n, "clicks": c, "impressions": i} for e, n, c, i in perf],
            "note": None if gsc_pages or perf else
            "No search-engine evidence in this run. Indexing is not inferred from HTTP status or sitemap membership."},
        "publications": ext.get("publication_discovery") and {
            "summary": ext["publication_discovery"].get("summary")} or {
            "gap": "No Publications hub capture in this run (supply --publication-hub-html to a Studio run)."},
        "change_events": [json.loads(r["details"]) for r in db.execute(
            "SELECT details FROM change_event WHERE run_id = ? ORDER BY batch", (run_id,))],
        "recommendations": _rows(db, "SELECT kind, COUNT(*) AS n FROM recommendation WHERE run_id = ? GROUP BY kind "
                                     "ORDER BY n DESC", (run_id,)),
    }


FILTERS = {
    "findings": "measured_findings > 0", "broken_out": "broken_links_out > 0", "not_200": "http_status <> 200",
    "no_description": "http_status = 200 AND has_description = 0", "multiple_h1": "h1_count > 1",
    "sitemap_problem": "sitemap_member = 1 AND (http_status <> 200 OR noindex = 1)",
    "kb_unverified": "post_type = 'sc_support_article' AND verification_state <> 'verified'",
    "structured_data": "structured_data IN ('invalid','unresolved')",
}


def pages(db, run_id, query="", flt="", offset=0):
    where, args = ["run_id = ?"], [run_id]
    if query:
        where.append("url LIKE ?")
        args.append("%" + query.replace("%", "") + "%")
    if flt:
        where.append(FILTERS[flt])
    sql = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) FROM page WHERE {sql}", args).fetchone()[0]
    rows = _rows(db, f"SELECT * FROM page WHERE {sql} ORDER BY measured_findings DESC, url LIMIT ? OFFSET ?",
                 args + [PAGE_SIZE, offset])
    return {"total": total, "offset": offset, "rows": rows}


def page_detail(db, run_id, url):
    return {
        "observations": [{**r, "facts": json.loads(r["facts"])} for r in _rows(
            db, "SELECT observation_id, source_kind, source_sha256, source_record, source_time_raw, facts FROM "
                "observation WHERE run_id = ? AND url = ? ORDER BY source_kind", (run_id, url))],
        "findings": _rows(db, "SELECT priority, action, evidence_class, origin, observation_id FROM finding WHERE "
                              "run_id = ? AND url = ? ORDER BY evidence_class, priority", (run_id, url)),
        "recommendations": [{**r, "basis": json.loads(r["basis"])} for r in _rows(
            db, "SELECT * FROM recommendation WHERE run_id = ? AND subject = ?", (run_id, url))]}


def recommendations(db, run_id, kind="", offset=0):
    where, args = "run_id = ?" + (" AND kind = ?" if kind else ""), [run_id] + ([kind] if kind else [])
    total = db.execute(f"SELECT COUNT(*) FROM recommendation WHERE {where}", args).fetchone()[0]
    rows = [{**r, "basis": json.loads(r["basis"])} for r in _rows(
        db, f"SELECT * FROM recommendation WHERE {where} ORDER BY kind, subject LIMIT ? OFFSET ?",
        args + [PAGE_SIZE, offset])]
    return {"total": total, "offset": offset, "rows": rows}


def make_handler(db, keyring, allowed, methods, workspace_session_url, workspace_origin):
    static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/app.css": ("app.css", "text/css; charset=utf-8")}
    csp = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
           f"connect-src 'self' {workspace_origin}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

    export_slot = threading.BoundedSemaphore(1)  # one workbook at a time; the store is shared and read-only

    class Handler(BaseHTTPRequestHandler):
        server_version = "SEOStudio"
        sys_version = ""

        def log_message(self, fmt, *args):  # no URLs or identities in logs
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            for k, v in {"Content-Type": ctype, "Content-Length": str(len(data)), "Cache-Control": "no-store",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                         "X-Frame-Options": "DENY", "Content-Security-Policy": csp, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def export(self, session, q):
            """Excel review workbook (SC-SEO-STUDIO-EXPORT-01): read-only, same session check as every API route."""
            if not export_slot.acquire(blocking=False):
                return self.send(429, {"error": "an export is already being prepared; try again shortly"},
                                 extra={"Retry-After": "10"})
            try:
                data, name, _ = seo_studio_export.export_workbook(
                    db, q["run"], scope=q.get("scope", "run"), flt=q.get("filter", ""), query=q.get("q", ""),
                    kind=q.get("kind", ""), filters=FILTERS, subject=session["subject"])
            finally:
                export_slot.release()
            return self.send(200, data, seo_studio_export.XLSX_TYPE,
                             extra={"Content-Disposition": f'attachment; filename="{name}"'})

        def do_GET(self):  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path in static:
                name, ctype = static[parts.path]
                body = (STATIC / name).read_bytes()
                if name == "index.html":
                    body = body.replace(b"__SESSION_URL__", json.dumps(workspace_session_url).encode()[1:-1])
                return self.send(200, body, ctype)
            if not parts.path.startswith("/api/"):
                return self.send(404, {"error": "not found"})
            try:
                session = verify_session(self.headers.get("Authorization"), keyring, allowed, methods)
            except SessionRefused as exc:
                return self.send(401, {"error": str(exc)}, extra={"WWW-Authenticate": "SC-Workspace"})
            q = {k: v[0] for k, v in parse_qs(parts.query).items()}
            try:
                offset = max(0, int(q.get("offset", "0")))
                if parts.path == "/api/runs":
                    return self.send(200, {"subject": session["subject"], "runs": seo_store.runs(db)})
                if parts.path == "/api/overview":
                    return self.send(200, overview(db, q["run"]))
                if parts.path == "/api/pages":
                    if q.get("filter", "") not in ("", *FILTERS):
                        return self.send(400, {"error": "unknown filter"})
                    return self.send(200, pages(db, q["run"], q.get("q", "")[:200], q.get("filter", ""), offset))
                if parts.path == "/api/page":
                    return self.send(200, page_detail(db, q["run"], q["url"]))
                if parts.path == "/api/recommendations":
                    return self.send(200, recommendations(db, q["run"], q.get("kind", "")[:40], offset))
                if parts.path == "/api/export.xlsx":
                    return self.export(session, q)
                if parts.path == "/api/compare":
                    return self.send(200, seo_store.compare(db, q["before"], q["after"]))
            except KeyError:
                return self.send(400, {"error": "missing or unknown run"})
            except ValueError:
                return self.send(400, {"error": "invalid parameter"})
            return self.send(404, {"error": "not found"})

    return Handler


def main(argv=None):
    cli = argparse.ArgumentParser(description="SEO Studio (read-only, Workspace-authenticated)")
    cli.add_argument("--db", required=True, type=Path)
    cli.add_argument("--keyring", required=True, type=Path, help="Pinned Workspace public keyring JSON")
    cli.add_argument("--allow", action="append", required=True, help="Allowed subject, e.g. workspace-user:<key>")
    cli.add_argument("--session-method", action="append", default=[], help="Extra accepted sign-in method")
    cli.add_argument("--workspace-session-url", required=True)
    cli.add_argument("--bind", default="127.0.0.1")
    cli.add_argument("--port", type=int, default=18980)
    args = cli.parse_args(argv)
    db = seo_store.connect(args.db, readonly=True)
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(args.workspace_session_url))
    handler = make_handler(db, Keyring.load(args.keyring), frozenset(args.allow), frozenset(args.session_method),
                           args.workspace_session_url, origin)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"SEO Studio on http://{args.bind}:{args.port}/ (read-only store; Workspace sessions required)")
    server.serve_forever()


if __name__ == "__main__":
    main()
