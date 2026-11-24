"""Structured-data (JSON-LD) validation for captured HTML, ported from SC-SEO-01 ``ldcheck.py``.

The SC-SEO-01 tool fetched pages itself. This port never makes requests: it validates JSON-LD blocks that an
existing collector (bounded crawl, SiteOne capture or the SC-SEO-01 baseline) already captured, so there is one
collection engine and one validator. ``validate_blocks`` returns the same keys and values as the original tool's
structured-data fields; page fields the original also printed (title, description, robots, H1) come from the
collectors and are not re-derived here.
"""
import json
import re

VERSION = "1.0.0"
BLOCK = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S)
ID_REF = re.compile(r'"@id":\s*"([^"]+)"')
ARTICLE_TYPES = ("Article", "TechArticle", "BlogPosting", "NewsArticle")
ARTICLE_REQUIRED = ("headline", "datePublished", "author", "publisher")
MAX_BLOCK_BYTES = 2 * 1024 * 1024


def extract_blocks(html):
    """JSON-LD script bodies, in document order, exactly as the original tool selected them."""
    return BLOCK.findall(html or "")


def _types(node):
    value = node.get("@type") if isinstance(node, dict) else None
    return value if isinstance(value, list) else [value]


def validate_blocks(blocks):
    """Validate captured JSON-LD blocks. Never raises on malformed input; reports it as ``parse``."""
    blocks = list(blocks or [])
    out = {"validator": "seo_structured_data/" + VERSION, "blocks": len(blocks)}
    try:
        if any(len(b.encode("utf-8")) > MAX_BLOCK_BYTES for b in blocks):
            raise ValueError("JSON-LD block exceeds size limit")
        graphs = [json.loads(b) for b in blocks]
        nodes = [n for g in graphs for n in ((g.get("@graph") if isinstance(g, dict) else None) or [g])]
        ids = set()

        def walk(value):
            if isinstance(value, dict):
                if "@type" in value and "@id" in value:
                    ids.add(value["@id"])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(graphs)
        refs = set(ID_REF.findall(json.dumps(graphs)))
        dict_nodes = [n for n in nodes if isinstance(n, dict)]
        out["types"] = sorted({t for n in dict_nodes for t in _types(n) if t})
        out["unresolved"] = sorted(r for r in refs - ids if "#" in r and "gravatar" not in r)
        publishers = [n for n in dict_nodes
                      if str(n.get("@id", "")).endswith(("/#organization", "/#person"))]
        out["publisher"] = [n.get("@type") for n in publishers]
        out["website_name"] = [n.get("name") for n in dict_nodes if n.get("@type") == "WebSite"]
        articles = [n for n in dict_nodes if n.get("@type") in ARTICLE_TYPES]
        out["article_missing"] = sorted({k for a in articles for k in ARTICLE_REQUIRED if k not in a})
        out["parse"] = "ok"
    except Exception as exc:  # the original reported any failure as a parse error; keep that contract
        out["parse"] = "ERROR " + str(exc)
    return out


def findings(result):
    """Measured defects from one validation result: (priority, action) pairs, no quality judgement."""
    checks = []
    if result["parse"] != "ok":
        checks.append(("P1", "Repair invalid JSON-LD (" + result["parse"][:80] + ")"))
        return checks
    if result["blocks"] == 0:
        return checks  # absence is recorded as a fact, not as a defect
    if result["blocks"] > 1:
        checks.append(("P3", "Review multiple JSON-LD blocks"))
    if result["unresolved"]:
        checks.append(("P2", "Resolve JSON-LD @id references: " + ", ".join(result["unresolved"][:3])))
    if result["article_missing"]:
        checks.append(("P3", "Complete article schema fields: " + ", ".join(result["article_missing"])))
    return checks
