"""Evidence-backed optimisation opportunities (inferred), kept separate from measured findings.

Every recommendation names the observations it rests on, is marked ``evidence_class = inferred``, needs human
approval, and changes nothing: a change reaches a website only through ``remediation/`` after a separate,
per-batch authorization. Rules never upgrade a governance state, never propose indexing unverified content and
never invent relationships between pages.
"""
from collections import defaultdict
import hashlib

VERSION = "1.0.0"
NO_CHANGE = "No production change. Applying it needs an approved proposal and an authorized remediation batch."
LOW_CTR_MIN_IMPRESSIONS = 100
LOW_CTR = 0.01


def _rid(kind, subject):
    return hashlib.sha256(f"{kind}:{subject}".encode()).hexdigest()[:20]


def _rec(kind, subject, summary, basis):
    return {"recommendation_id": _rid(kind, subject), "kind": kind, "subject": subject, "summary": summary,
            "evidence_class": "inferred", "approval": "required", "production_change": NO_CHANGE,
            "basis": basis, "rules": "seo_recommendations/" + VERSION}


def _latest(page, kind, predicate=lambda facts: True):
    rows = [o for o in page["observations"] if o["source_kind"] == kind and predicate(o["facts"])]
    return rows[-1] if rows else None


def derive(history):
    pages = {p["url"]: p for p in history["pages"]}
    out = []
    baseline = history.get("baseline") or {}
    events = {e["batch"]: e for e in history.get("change_events") or []}

    # 1. Broken internal links: validated redirect proposals, held proposals, and link repairs.
    inlinks = defaultdict(list)
    for page in history["pages"]:
        for o in page["observations"]:
            if "broken_link" in o["facts"]:
                inlinks[o["facts"]["broken_link"]].append(o["observation_id"])
    origin = history["property_scope"].rstrip("/")
    proposals = {}
    for group in ("approved", "held_for_review"):
        for r in (baseline.get("redirect_verification") or {}).get(group, {}).get("results", []):
            proposals[origin + r["source"]] = (group, r)
    for target, obs in sorted(inlinks.items()):
        group, result = proposals.get(target, (None, None))
        if group == "approved" and result["verdict"] == "pass":
            b3 = events.get("B3", {})
            out.append(_rec("redirect", target,
                            f"301 {result['source']} → {result['target']}: validated (source 404, target 200, "
                            f"self-canonical, indexable); {len(obs)} internal link(s) affected. "
                            f"Batch B3 state: {b3.get('state', 'unknown')}.",
                            {"broken_link_observations": obs[:50], "validator": result,
                             "change_event": b3.get("batch")}))
        elif group == "held_for_review":
            out.append(_rec("redirect_decision", target,
                            f"Decide whether {result['source']} has a genuine equivalent; the proposed target "
                            f"{result['target']} was held for topical mismatch. {len(obs)} internal link(s) affected.",
                            {"broken_link_observations": obs[:50], "validator": result}))
        else:
            out.append(_rec("link_repair", target,
                            f"Update or remove {len(obs)} internal link(s) to {target} (observed failing); "
                            "redirect only to a genuine equivalent.", {"broken_link_observations": obs[:50]}))

    for url, page in sorted(pages.items()):
        crawl = _latest(page, "http_crawl", lambda f: f.get("http_status") == 200)
        if crawl is None:
            continue
        facts = crawl["facts"]
        robots = " ".join((facts.get("robots_meta") or []) + [facts.get("x_robots") or ""]).lower()
        indexable = "noindex" not in robots
        in_sitemap = page.get("sitemap_member") is True
        # 2. Metadata: only for indexable sitemap pages, drafted from the page's own text by an editor.
        if indexable and in_sitemap and not facts.get("page_description"):
            out.append(_rec("meta_description", url, "Draft a page-specific meta description from the page's own "
                            "text for editorial review.", {"observation": crawl["observation_id"]}))
        # 3. Headings.
        if indexable and (facts.get("h1_count") or 0) > 1:
            out.append(_rec("headings", url, f"Review {facts['h1_count']} H1 elements; keep the template H1 and "
                            "demote repeated or widget headings.",
                            {"observation": crawl["observation_id"], "h1": facts.get("h1")}))
        # 4. Structured data.
        sd = facts.get("structured_data") or {}
        if sd and (sd.get("parse") != "ok" or sd.get("unresolved")):
            out.append(_rec("structured_data", url, "Repair JSON-LD so it parses and every @id reference resolves.",
                            {"observation": crawl["observation_id"], "validation": sd}))
        # 5. Search performance (only where search-engine evidence exists).
        perf = _latest(page, "gsc_api", lambda f: "impressions" in f)
        if perf and perf["facts"]["impressions"] >= LOW_CTR_MIN_IMPRESSIONS and \
                (perf["facts"].get("ctr") or 0) < LOW_CTR:
            out.append(_rec("search_snippet", url,
                            f"{perf['facts']['impressions']} impressions at CTR {perf['facts'].get('ctr', 0):.2%}: "
                            "review title and description against the queries this page appears for.",
                            {"search_observation": perf["observation_id"], "page_observation": crawl["observation_id"]}))

    # 6. Knowledge Base governance: never promote unverified articles.
    unverified, verified_unlisted = [], []
    for url, page in sorted(pages.items()):
        wp = _latest(page, "wordpress", lambda f: f.get("post_type") == "sc_support_article" and "verification_state" in f)
        if wp is None:
            continue
        if wp["facts"]["verification_state"] != "verified":
            unverified.append(wp["observation_id"])
        elif page.get("sitemap_member") is not True:
            verified_unlisted.append((url, wp["observation_id"]))
    if unverified:
        out.append(_rec("kb_governance", "knowledge-base:unverified",
                        f"Keep {len(unverified)} Knowledge Base articles whose recorded verification state is not "
                        "'verified' out of sitemaps; inclusion follows the Knowledge Base's own verification.",
                        {"wordpress_observations": unverified[:100], "count": len(unverified)}))
    for url, oid in verified_unlisted:
        out.append(_rec("kb_sitemap_candidate", url, "Verified Knowledge Base article not in a sitemap: candidate "
                        "for inclusion after owner review.", {"wordpress_observation": oid}))
    return out
