"""Offline Publications link diagnostics for SEO Intelligence Studio."""
from collections import Counter, defaultdict, deque
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from seo_evidence import source_bytes
from seo_siteone import ImportErrorEvidence, url_key

SCHEMA = "seo.publication-discovery.v1"


def target(base, value, scope):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        absolute = urljoin(base, value)
    except ValueError:
        return None
    return url_key(absolute, scope)


class Snapshot(HTMLParser):
    def __init__(self, url, raw, scope):
        super().__init__()
        self.url, self.scope = url, scope
        self.main = False
        self.template_depth = 0
        self.in_head = False
        self.json_script = None
        self.links = set()
        self.main_links = set()
        self.fields = []
        self.canonicals = []
        self.robots = []
        self.invalid_payloads = 0
        self.feed(raw.decode("utf-8-sig"))
        self.close()

    def handle_starttag(self, tag, items):
        attrs = dict(items)
        if tag == "template":
            self.template_depth += 1
        if self.template_depth:
            return
        if tag == "head":
            self.in_head = True
        if tag == "main":
            self.main = True
        if tag == "a":
            url = target(self.url, attrs.get("href"), self.scope)
            if url:
                self.links.add(url)
                if self.main:
                    self.main_links.add(url)
        if self.in_head and tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonicals.append(attrs.get("href"))
        if self.in_head and tag == "meta" and (attrs.get("name") or "").lower() in ("robots", "googlebot"):
            self.robots.append(attrs.get("content"))
        if tag == "script" and attrs.get("type") == "application/json":
            self.json_script = []

    def handle_data(self, data):
        if self.json_script is not None:
            self.json_script.append(data)

    def handle_endtag(self, tag):
        if tag == "template" and self.template_depth:
            self.template_depth -= 1
            return
        if self.template_depth:
            return
        if tag == "head":
            self.in_head = False
        if tag == "main":
            self.main = False
        if tag == "script" and self.json_script is not None:
            data = "".join(self.json_script)
            self.json_script = None
            try:
                payload = json.loads(data)
            except ValueError:
                self.invalid_payloads += 1
                return
            field = payload.get("field") if isinstance(payload, dict) else None
            if isinstance(field, dict) and isinstance(field.get("panels"), list):
                self.fields.append(field)


def analyze_publications(sources, rendered_sources, evidence):
    scope = evidence["property_scope"]
    raw_pages, rendered_pages, artifacts = {}, {}, []
    for kind, items, pages in (("initial_html", sources, raw_pages),
                               ("rendered_html", rendered_sources, rendered_pages)):
        for url, file in items:
            key = url_key(url, scope)
            if not key or key in pages:
                raise ImportErrorEvidence("Publication snapshots require distinct in-scope URL keys")
            raw = source_bytes(file)
            pages[key] = Snapshot(key, raw, scope)
            artifacts.append({"url": key, "kind": kind, "file": file.name,
                              "sha256": hashlib.sha256(raw).hexdigest()})
    hub = url_key(scope.rstrip("/") + "/publications/", scope)
    if hub not in raw_pages:
        raise ImportErrorEvidence("Supply the initial HTML for /publications/")
    observed = raw_pages[hub]
    if not observed.fields:
        raise ImportErrorEvidence("No recognized Publications field payloads in hub HTML")
    fields, maps, edges = [], [], []
    identities = set()
    article_to_maps = defaultdict(set)
    for block in observed.fields:
        field_key = block.get("key")
        if not isinstance(field_key, str):
            raise ImportErrorEvidence("Invalid Publications field")
        archive = target(hub, block.get("browse_url"), scope)
        fields.append({"key": field_key, "archive_url": archive,
                       "hub_main_anchor": archive in observed.main_links,
                       "archive_snapshot_available": archive in raw_pages})
        for panel in block["panels"]:
            if not isinstance(panel, dict):
                raise ImportErrorEvidence("Invalid Article Map panel")
            panel_id = panel.get("key")
            articles = panel.get("articles", [])
            if not isinstance(panel_id, str) or not isinstance(articles, list):
                raise ImportErrorEvidence("Invalid Article Map identity or article list")
            identity = f"{field_key}:{panel_id}"
            if identity in identities:
                raise ImportErrorEvidence("Duplicate Article Map panel identity")
            identities.add(identity)
            map_url = target(hub, panel.get("canonical_url"), scope)
            urls = []
            for article in articles:
                if not isinstance(article, dict):
                    raise ImportErrorEvidence("Invalid Article Map article")
                key = target(hub, article.get("url"), scope)
                if key:
                    urls.append(key)
                    article_to_maps[key].add(identity)
                    edges.append({"from": identity, "to": key, "kind": "registry_association"})
            map_snapshot = raw_pages.get(map_url)
            maps.append({"id": identity, "field": field_key, "url": map_url, "article_urls": urls,
                         "hub_main_anchor": map_url in observed.main_links,
                         "map_snapshot_available": map_snapshot is not None,
                         "articles_linked_from_map_snapshot": (
                             sorted(set(urls) & map_snapshot.links) if map_snapshot else None)})
    graph = {url: sorted(page.links) for url, page in raw_pages.items()}
    depth = {hub: 0}
    pending = deque([hub])
    while pending:
        source = pending.popleft()
        for dest in graph.get(source, []):
            if dest not in depth:
                depth[dest] = depth[source] + 1
                pending.append(dest)
    for map_row in maps:
        map_row["observed_depth"] = depth.get(map_row["url"])
    rendered = rendered_pages.get(hub)
    by_url = {page["url"]: page for page in evidence["pages"]}
    observations = []
    for url in sorted(article_to_maps):
        page = by_url.get(url, {})
        facts = page.get("observations", [])
        snapshot = raw_pages.get(url)
        declarations = ([target(url, value, scope) for value in snapshot.canonicals]
                        if snapshot else None)
        observations.append({
            "url": url, "map_ids": sorted(article_to_maps[url]),
            "hub_main_anchor": url in observed.main_links, "hub_document_anchor": url in observed.links,
            "reachable_in_captured_graph": url in depth, "observed_depth": depth.get(url),
            "rendered_hub_anchor": url in rendered.links if rendered else None,
            "sitemap_member": page.get("sitemap_member"),
            "canonical_declared_in_capture": declarations,
            "canonical_status": ("unknown" if declarations is None else "self" if declarations == [url]
                                 else "missing" if not declarations else "different_or_multiple"),
            "google_export_classes": sorted({o["facts"]["export_classification"] for o in facts
                                             if o["source_kind"] == "gsc_export"}),
            "http_observations": [{"source": o["source_kind"], "status": o["facts"]["http_status"],
                                   "observation_id": o["observation_id"]}
                                  for o in facts if "http_status" in o["facts"]],
            "wordpress_status_observations": sorted({o["facts"]["status"] for o in facts
                                                       if o["source_kind"] == "wordpress"}),
            "evidence_ids": [o["observation_id"] for o in facts],
        })
    variants = []
    for url in graph[hub]:
        parts = urlsplit(url)
        if urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) == hub and parts.query:
            snapshot = raw_pages.get(url)
            declarations = ([target(url, value, scope) for value in snapshot.canonicals]
                            if snapshot else None)
            variants.append({
                "url": url, "parameters": sorted({k for k, _ in parse_qsl(parts.query)}),
                "canonical_declared_in_capture": declarations,
                "duplicate_content_verified": None,
            })
    pagination = [url for url in graph[hub] if re.search(r"/page/\d+/?$", urlsplit(url).path)
                  or any(k == "paged" for k, _ in parse_qsl(urlsplit(url).query))]
    duplicate_maps = Counter(row["url"] for row in maps if row["url"])
    google_cohorts = {}
    for classification in ("indexed", "unindexed", "403"):
        cohort = [a for a in observations if classification in a["google_export_classes"]]
        google_cohorts[classification] = {
            "matches": len(cohort),
            "linked_in_initial_main": sum(a["hub_main_anchor"] for a in cohort),
            "reachable_in_captured_graph": sum(a["reachable_in_captured_graph"] for a in cohort),
        }
    return {"schema_version": SCHEMA, "google_export_cohorts": google_cohorts, "hub": hub, "artifacts": artifacts, "fields": fields, "maps": maps,
            "parameter_variants": variants, "archive_targets": [f["archive_url"] for f in fields],
            "pagination_targets": pagination, "registry_edges": edges, "html_adjacency": graph,
            "articles": observations, "duplicate_map_url_counts": {k:v for k,v in duplicate_maps.items() if v > 1},
            "hub_canonicals": observed.canonicals, "hub_robots_meta": observed.robots,
            "invalid_json_scripts": observed.invalid_payloads, "summary": {
                "fields": len(fields), "maps": len(maps), "article_slots": len(edges),
                "distinct_articles": len(observations), "maps_linked_in_initial_main": sum(m["hub_main_anchor"] for m in maps),
                "articles_linked_in_initial_main": sum(a["hub_main_anchor"] for a in observations),
                "articles_linked_in_initial_document": sum(a["hub_document_anchor"] for a in observations),
                "maps_without_article_slots": sum(not m["article_urls"] for m in maps),
                "article_targets_without_path_in_captures": sum(not a["reachable_in_captured_graph"] for a in observations),
                "parameter_variants": len(variants), "initial_snapshots": len(raw_pages),
                "rendered_snapshots": len(rendered_pages),
            }}


def compare_publication_discovery(baseline, proposed):
    """Compare the same property and hub. Unknown observed depth stays null."""
    if (baseline.get("schema_version") != SCHEMA or proposed.get("schema_version") != SCHEMA
            or baseline.get("hub") != proposed.get("hub")):
        raise ImportErrorEvidence("Comparison requires same-hub publication discovery v1 reports")

    def compare_rows(before, after, identity):
        old = {r[identity]: r for r in before}
        new = {r[identity]: r for r in after}
        return [{
            "id": key,
            "before_url": old.get(key, {}).get("url"),
            "after_url": new.get(key, {}).get("url"),
            "before_main_anchor": old.get(key, {}).get("hub_main_anchor"),
            "after_main_anchor": new.get(key, {}).get("hub_main_anchor"),
            "before_depth": old.get(key, {}).get("observed_depth"),
            "after_depth": new.get(key, {}).get("observed_depth"),
        } for key in sorted(old.keys() | new.keys())]

    map_rows = compare_rows(baseline["maps"], proposed["maps"], "id")
    articles = compare_rows(baseline["articles"], proposed["articles"], "url")
    field_coverage = []
    for field in sorted({m["field"] for p in (baseline, proposed) for m in p["maps"]}):
        counts = []
        for data in (baseline, proposed):
            maps = [m for m in data["maps"] if m["field"] == field]
            targets = {url for m in maps for url in m["article_urls"]}
            articles_by_url = {a["url"]: a for a in data["articles"]}
            counts.append({
                "map_panels": len(maps),
                "maps_with_main_anchor": sum(m["hub_main_anchor"] for m in maps),
                "article_targets": len(targets),
                "articles_with_main_anchor": sum(articles_by_url[u]["hub_main_anchor"] for u in targets),
            })
        field_coverage.append({"field": field, "before": counts[0], "after": counts[1]})
    return {
        "schema_version": "seo.publication-comparison.v1",
        "hub": proposed["hub"],
        "source_artifacts": {"before": baseline["artifacts"], "after": proposed["artifacts"]},
        "counts_before": baseline["summary"],
        "counts_after": proposed["summary"],
        "observed_hub_canonical_before": baseline["hub_canonicals"],
        "observed_hub_canonical_after": proposed["hub_canonicals"],
        "observed_hub_robots_before": baseline["hub_robots_meta"],
        "observed_hub_robots_after": proposed["hub_robots_meta"],
        "changed_map_urls": sum(r["before_url"] != r["after_url"] for r in map_rows),
        "duplicate_map_urls": {"before": baseline["duplicate_map_url_counts"],
                               "after": proposed["duplicate_map_url_counts"]},
        "map_depths": map_rows,
        "article_depths": articles,
        "field_coverage": field_coverage,
        "scope_note": "Capture-graph depth and link coverage only. Sitewide orphaning is unknown.",
    }
