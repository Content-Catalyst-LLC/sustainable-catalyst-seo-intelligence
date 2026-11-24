"""Proposed-redirect verification, ported from SC-SEO-01 ``check_redirects.py``.

The original tool fetched every source and target itself. This port evaluates the same rules against observations
supplied by an existing collector, so verification reuses the one collection engine and its rate and robots policy:

* ``observations_from_history`` reads an evidence history (baseline crawl, bounded crawl, SiteOne), and
* a collector run over exactly the proposal URLs gives current observations.

Rules (unchanged): self-loop, target is also a source, source or target already redirected by an existing rule,
source not currently 404, target not 200, target canonical not the target itself, target noindex. A proposal is
never applied here; the result is a review record for the recommendation pipeline.
"""
import csv
import io

VERSION = "1.0.0"
FIELDS = ("source", "target", "source_status", "target_status", "target_canonical", "target_noindex",
          "observation_ids", "problems", "verdict")


def _norm(path):
    return path.rstrip("/") + "/"


def read_proposals(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not {"source", "target"} <= set(rows[0]):
        raise ValueError("Proposals need source and target columns")
    for row in rows:
        for key in ("source", "target"):
            if not str(row.get(key) or "").startswith("/"):
                raise ValueError("Proposal paths must be site-relative")
    return rows


def observations_from_history(history, origin):
    """{path: observation} for http_crawl / siteone rows, latest-imported wins per path."""
    origin = origin.rstrip("/")
    found = {}
    for page in history["pages"]:
        if not page["url"].startswith(origin + "/"):
            continue
        path = page["url"][len(origin):]
        for row in page["observations"]:
            facts = row["facts"]
            if row["source_kind"] not in ("http_crawl", "siteone") or "http_status" not in facts:
                continue
            canonicals = facts.get("declared_canonicals")
            if canonicals is None and facts.get("declared_canonical"):
                canonicals = [facts["declared_canonical"]]
            robots = " ".join(facts.get("robots_meta") or []) + " " + str(facts.get("robots_index_raw") or "")
            found[path] = {
                "status": facts["http_status"],
                "canonicals": canonicals or [],
                "noindex": "noindex" in robots.lower().replace(",", " ").split()
                or str(facts.get("robots_index_raw")).lower() in ("0", "false"),
                "observation_id": row["observation_id"],
            }
    return found


def verify(proposals, existing_sources, observations, origin):
    """Apply the original rules. ``existing_sources`` are paths of current redirect rules."""
    origin = origin.rstrip("/")
    existing = {_norm(p) for p in existing_sources}
    sources = {row["source"] for row in proposals}
    results = []
    for row in proposals:
        source, target = row["source"], row["target"]
        problems = []
        if source == target:
            problems.append("self-loop")
        if target in sources:
            problems.append("target is also a source (chain/loop)")
        if _norm(source) in existing:
            problems.append("source already in Redirection")
        if _norm(target) in existing:
            problems.append("target already redirected (chain)")
        s_obs, t_obs = observations.get(source), observations.get(target)
        s_status = s_obs["status"] if s_obs else None
        t_status = t_obs["status"] if t_obs else None
        if s_obs is None:
            problems.append("source not observed (unknown)")
        elif s_status != 404:
            problems.append(f"source currently {s_status} (would shadow live URL)")
        if t_obs is None:
            problems.append("target not observed (unknown)")
        elif t_status != 200:
            problems.append(f"target {t_status}")
        canonical = None
        if t_obs and t_status == 200:
            canonical = t_obs["canonicals"][0] if t_obs["canonicals"] else None  # original: first <link rel=canonical>
            if canonical != origin + target:
                problems.append(f"target canonical {canonical}")
            if t_obs["noindex"]:
                problems.append("target noindex")
        unknown = any(p.endswith("(unknown)") for p in problems)
        results.append({
            "source": source, "target": target, "source_status": s_status, "target_status": t_status,
            "target_canonical": canonical, "target_noindex": t_obs["noindex"] if t_obs else None,
            "observation_ids": [o["observation_id"] for o in (s_obs, t_obs) if o],
            "problems": problems,
            "verdict": "unverified" if unknown else ("fail" if problems else "pass"),
        })
    return results


def summary(results):
    return {"validator": "seo_redirect_proposals/" + VERSION, "checked": len(results),
            "pass": sum(r["verdict"] == "pass" for r in results),
            "fail": sum(r["verdict"] == "fail" for r in results),
            "unverified": sum(r["verdict"] == "unverified" for r in results)}
