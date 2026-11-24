"""Build a property-scoped, offline evidence history for the existing Studio."""
from collections import Counter, defaultdict
import hashlib
from html.parser import HTMLParser
import json
import xml.etree.ElementTree as ET

from seo_structured_data import findings as structured_findings
from seo_siteone import ImportErrorEvidence, MAX_BYTES, MAX_RECORDS, resolve_target, url_key, utc_timestamp

SCHEMA = "seo.evidence-history.v1"
# Optional WordPress/Knowledge Base governance states kept from an authorized export. Values are copied verbatim:
# the Studio reports them and never upgrades an unverified state or implies editorial approval.
GOVERNANCE_FIELDS = ("post_type", "verification_state", "editorial_state", "lifecycle", "human_review_required",
                     "kb_verification", "foundation_status", "document_intelligence_public", "robots")
HTTP_KINDS = ("siteone", "http_crawl")
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def source_bytes(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ImportErrorEvidence("Evidence artifact exceeds ingestion limit")
    return raw


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_head = False
        self.canonicals = []
        self.robots = []

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        if tag == "body":
            self.in_head = False
        if not self.in_head:
            return
        attrs = dict(attrs)
        if tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonicals.append(attrs.get("href") or "")
        if tag == "meta" and (attrs.get("name") or "").lower() == "robots":
            self.robots.append(attrs.get("content") or "")


class EvidenceHistory:
    def __init__(self, scope, imported_at=None):
        self.scope = scope
        self.imported_at = utc_timestamp(imported_at)
        self.records = {}
        self.artifacts = {}
        self.rejected = Counter()
        self.extensions = {}  # run-level sections added by importers (e.g. a versioned baseline manifest)

    def artifact(self, kind, path, raw):
        digest = hashlib.sha256(raw).hexdigest()
        self.artifacts.setdefault((kind, digest), {
            "source_kind": kind, "source_file": path.name, "source_sha256": digest,
            "imported_at_utc": self.imported_at,
        })
        return digest

    def add(self, kind, digest, ordinal, url, facts, observed=None, observation_id=None):
        key = url_key(url, self.scope)
        if not key:
            self.rejected[kind] += 1
            return
        identity = observation_id or hashlib.sha256(
            f"{kind}:{digest}:{ordinal}".encode()
        ).hexdigest()
        self.records.setdefault(identity, {
            "observation_id": identity, "source_kind": kind, "source_sha256": digest,
            "source_record": ordinal, "original_url": url, "comparison_key": key,
            "source_time_raw": observed, "facts": facts,
        })

    def siteone(self, parsed):
        receipt = parsed["receipt"]
        digest = receipt["source_sha256"]
        if ("siteone", digest) in self.artifacts:
            return
        self.artifacts[("siteone", digest)] = receipt
        self.rejected["siteone"] += sum(receipt["rejected"].values())
        fields = ("http_status", "redirect_target_url", "declared_canonical", "page_h1",
                  "robots_index_raw", "robots_indexing_raw", "robots_denied_raw",
                  "page_title", "page_description")
        for row in parsed["observations"]:
            self.add("siteone", digest, None, row["original_url"],
                     {field: row[field] for field in fields}, row["source_observed_at_raw"],
                     row["observation_id"])

        for relation, index, source, target in parsed["relationships"]:
            identity = hashlib.sha256(f"siteone:{digest}:{relation}:{index}".encode()).hexdigest()
            self.add("siteone", digest, index, source, {relation: target},
                     receipt["crawler_executed_at_raw"], identity)

    def html_response(self, url, path, observed_at=None):
        if not url_key(url, self.scope):
            raise ImportErrorEvidence("HTML response URL is outside the property")
        raw = source_bytes(path)
        metadata = PageMetadata()
        metadata.feed(raw.decode("utf-8-sig"))
        metadata.close()
        digest = self.artifact("html_response", path, raw)
        observed = utc_timestamp(observed_at) if observed_at is not None else None
        # Same bytes may be assigned to different response URLs by the collector.
        identity = hashlib.sha256(f"html_response:{digest}:{url}:{observed}".encode()).hexdigest()
        self.add("html_response", digest, None, url, {
            "declared_canonicals": [resolve_target(url, value) for value in metadata.canonicals],
            "html_robots_meta": metadata.robots,
        }, observed, identity)

    def sitemap(self, path):
        raw = source_bytes(path)
        # Sitemap input is an explicitly supplied local leaf document.
        if b"\x00" in raw or b"<!DOCTYPE" in raw.upper():
            raise ImportErrorEvidence("Sitemap must be UTF-8 XML without a DTD")
        root = ET.fromstring(raw.decode("utf-8-sig"))
        if root.tag != SITEMAP_NS + "urlset":
            raise ImportErrorEvidence("Supply leaf sitemap urlset files individually")
        digest = self.artifact("sitemap", path, raw)
        rows = root.findall(SITEMAP_NS + "url")
        if len(rows) > MAX_RECORDS:
            raise ImportErrorEvidence("Too many sitemap records")
        for index, row in enumerate(rows):
            self.add("sitemap", digest, index, row.findtext(SITEMAP_NS + "loc"),
                     {"sitemap_member": True, "last_modified_raw": row.findtext(SITEMAP_NS + "lastmod")})

    def publications(self, path):
        raw = source_bytes(path)
        rows = json.loads(raw)
        if isinstance(rows, dict) and isinstance(rows.get("rows"), list):
            rows = rows["rows"]  # WordPress status export envelope (exported_at_utc, source, rows)
        if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
            raise ImportErrorEvidence("Expected a bounded array of authorized publication metadata")
        digest = self.artifact("wordpress", path, raw)
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or any(
                not isinstance(row.get(field), str) or not row[field].strip()
                for field in ("publication_id", "url", "status")
            ):
                self.rejected["wordpress"] += 1
                continue
            observed = row.get("modified_at")
            if not isinstance(observed, str):
                observed = None
            facts = {key: row[key] for key in ("publication_id", "status")}
            for key in GOVERNANCE_FIELDS:
                if isinstance(row.get(key), str) and row[key].strip():
                    facts[key] = row[key].strip()[:120]
            for key in ("publicly_viewable", "password_protected", "exact_permalink"):
                if type(row.get(key)) is bool:
                    facts[key] = row[key]
            if isinstance(row.get("approved_map_urls"), list):
                facts["approved_map_urls"] = sorted({
                    url for url in row["approved_map_urls"]
                    if isinstance(url, str) and url_key(url, self.scope) == url
                })
            if isinstance(row.get("inventory_observed_at"), str):
                facts["inventory_observed_at"] = utc_timestamp(row["inventory_observed_at"])
            self.add("wordpress", digest, index, row["url"], facts, observed)

    def build(self):
        grouped = defaultdict(list)
        for record in self.records.values():
            grouped[record["comparison_key"]].append(record)
        health = Counter()
        crawl_health = Counter()
        pages = []
        for key, rows in sorted(grouped.items()):
            in_sitemap = any(row["source_kind"] == "sitemap" for row in rows)
            findings = []
            for row in rows:
                facts = row["facts"]
                checks = []
                status = facts.get("http_status")
                noindex = False
                canonical_issue = False
                if row["source_kind"] == "siteone" and "http_status" in facts:
                    health[str(status) if status is not None else "unknown"] += 1
                    if status and status >= 400:
                        checks.append(("P1", "Investigate failed HTTP response"))
                    if facts["redirect_target_url"]:
                        checks.append(("P2", "Review redirect target"))
                    noindex = str(facts["robots_index_raw"]).lower() in ("0", "false", "noindex")
                    if status == 200 and facts["page_title"] and facts["page_h1"] == "":
                        checks.append(("P3", "Review missing primary heading"))
                if row["source_kind"] == "http_crawl" and "http_status" in facts:
                    crawl_health[str(status) if status is not None else "transport_error"] += 1
                    if status and status >= 400:
                        checks.append(("P1", "Investigate failed HTTP response"))
                    if facts.get("redirect_hops"):
                        checks.append(("P2", "Review redirect target"))
                    if status == 200:
                        targets = facts.get("declared_canonicals") or []
                        canonical_issue = len(targets) != 1 or url_key(targets[0], self.scope) != key
                        if canonical_issue:
                            checks.append(("P2", "Review declared canonical"))
                        noindex = any("noindex" in d.lower().replace(",", " ").split()
                                      for d in (facts.get("robots_meta") or []) + [facts.get("x_robots") or ""])
                        h1 = facts.get("h1_count")
                        if h1 == 0:
                            checks.append(("P3", "Review missing primary heading"))
                        elif h1 and h1 > 1:
                            checks.append(("P3", "Review multiple H1 headings"))
                        if not facts.get("page_description"):
                            checks.append(("P3", "Add a meta description"))
                        if (facts.get("title_count") or 0) > 1 or (facts.get("description_count") or 0) > 1:
                            checks.append(("P2", "Remove duplicate title or description tags"))
                        if facts.get("structured_data"):
                            checks.extend(structured_findings(facts["structured_data"]))
                if row["source_kind"] == "audit_finding":
                    # A prior audit's classification, kept as recorded (not re-derived, not re-prioritised).
                    findings.append({"priority": facts["audit_priority"], "action": facts["defect"],
                                     "observation_id": row["observation_id"], "origin": facts["analysis"]})
                if facts.get("broken_link"):
                    checks.append(("P1", "Repair broken link to " + facts["broken_link"]))
                if row["source_kind"] == "html_response":
                    targets = facts["declared_canonicals"]
                    canonical_issue = len(targets) > 1 or any(
                        url_key(target, self.scope) != key for target in targets)
                    if canonical_issue:
                        checks.append(("P2", "Review declared canonical"))
                    noindex = any("noindex" in directive.lower().replace(",", " ").split()
                                  for directive in facts["html_robots_meta"])
                if noindex:
                    checks.append(("P2", "Review noindex directive"))
                if in_sitemap and (noindex or canonical_issue or (
                        row["source_kind"] in HTTP_KINDS and status and 300 <= status < 600)):
                    checks.append(("P1", "Review sitemap entry against technical findings"))
                findings.extend({"priority": priority, "action": action,
                                 "observation_id": row["observation_id"]}
                                for priority, action in checks)
            pages.append({"url": key, "sitemap_member": True if in_sitemap else None,
                          "observations": rows, "findings": sorted(findings, key=lambda finding: finding["priority"])})
        return {"schema_version": SCHEMA, "property_scope": self.scope,
                "artifacts": list(self.artifacts.values()), "rejected": dict(self.rejected),
                "crawl_http_counts": dict(health), "http_crawl_counts": dict(crawl_health),
                "pages": pages, **self.extensions}
