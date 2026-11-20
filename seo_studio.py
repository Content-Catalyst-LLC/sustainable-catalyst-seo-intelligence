#!/usr/bin/env python3
"""SC-SEO-02: offline, read-only Search Console evidence analysis.

Only Python standard-library modules are used. No network or write actions
against Google, WordPress, GitHub, or a live production site.
"""
from __future__ import annotations

import argparse
import io
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from seo_evidence import EvidenceHistory, source_bytes
from seo_publications import analyze_publications, compare_publication_discovery
from seo_siteone import csv_safe, parse_report, property_origin, url_key

VERSION = "0.1.0"
DOMAIN = "sustainablecatalyst.com"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def excel_rows(path: Path | io.BytesIO) -> dict[str, list[dict]]:
    """Read Google XLSX exports without loading formulas or executing macros."""
    ns = {"x": X}
    with zipfile.ZipFile(path) as z:
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target", "") for item in rels}
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.findall(".//x:t", ns))
                      for si in root.findall("x:si", ns)]
        result = {}
        for sheet in workbook.findall("x:sheets/x:sheet", ns):
            target = targets.get(sheet.get("{" + R + "}id"), "")
            if not target:
                continue
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            member = member.replace("xl/xl/", "xl/")
            if ".." in Path(member).parts or member not in z.namelist():
                raise ValueError("Invalid XLSX worksheet reference")
            root = ET.fromstring(z.read(member))
            rows = []
            for row in root.findall("x:sheetData/x:row", ns):
                out = []
                for cell in row.findall("x:c", ns):
                    m = re.match(r"[A-Z]+", cell.get("r", "A1"))
                    column = 0
                    for letter in m.group() if m else "A":
                        column = column * 26 + ord(letter) - 64
                    while len(out) < column:
                        out.append(None)
                    typ = cell.get("t", "")
                    v = cell.find("x:v", ns)
                    value = v.text if v is not None else None
                    if typ == "s" and value is not None:
                        value = shared[int(value)]
                    elif typ == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//x:t", ns))
                    elif typ == "b" and value is not None:
                        value = value == "1"
                    elif value is not None and typ not in ("str", "e"):
                        try:
                            number = float(value)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            pass
                    out[column - 1] = value
                rows.append(out)
            if rows:
                headers = [str(x or "").strip() for x in rows[0]]
                result[sheet.get("name", "Sheet")] = [
                    {key: row[i] if i < len(row) else None
                     for i, key in enumerate(headers) if key}
                    for row in rows[1:] if any(v not in (None, "") for v in row)
                ]
            else:
                result[sheet.get("name", "Sheet")] = []
    return result


def normalize(raw: object) -> str:
    try:
        parsed = urlsplit(str(raw or "").strip())
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme.lower() not in ("http", "https") or host != DOMAIN:
            return ""
        path = re.sub(r"/+", "/", unquote(parsed.path or "/"))
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/") and not re.search(r"\.[a-z0-9]{2,6}$", path.lower()):
            path += "/"
        return "https://" + DOMAIN + path + ("?" + parsed.query if parsed.query else "")
    except ValueError:
        return ""


def date_value(raw: object) -> str:
    if raw in (None, ""):
        return ""
    if isinstance(raw, (float, int)) and 25000 < raw < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(raw))).date().isoformat()
    return str(raw)[:10]


def page_type(url: str) -> str:
    p = urlsplit(url)
    path = p.path.lower()
    if path == "/":
        return "Homepage"
    if path.startswith("/wp-json/") or "rest_route=" in p.query:
        return "REST API"
    if path.endswith("/feed/"):
        return "Feed"
    if path.startswith("/category/"):
        return "Category archive"
    if path.startswith(("/tag/", "/author/")) or re.search(r"/page/\d+/$", path):
        return "Other archive"
    if path.startswith("/support/"):
        return "Knowledge Base"
    if path.startswith("/research-hub/"):
        return "Research Hub"
    if path.startswith(("/workspace/", "/lab/", "/platform/", "/modeling-analytics/")):
        return "Application"
    if re.search(r"\.(pdf|png|jpe?g|webp|svg|css|js|xml)$", path):
        return "Asset"
    if len([part for part in path.split("/") if part]) == 1:
        return "Article / page"
    return "Nested page"


def source_type(sheets: dict) -> str:
    if "Critical issues" in sheets and "Chart" in sheets:
        return "coverage"
    issue = " ".join(str(r.get("Value") or "") for r in sheets.get("Metadata", [])
                     if str(r.get("Property") or "").lower() == "issue").lower()
    if "crawled - currently not indexed" in issue:
        return "unindexed"
    if "forbidden" in issue or "(403)" in issue:
        return "403"
    if sheets.get("Table") and "URL" in sheets["Table"][0]:
        return "indexed"
    return ""


def action_for(row: dict) -> tuple[str, str]:
    t, status, http = row["type"], row["indexing_status"], row["http_status"]
    if http == "403":
        return "P1", "Investigate current 403; do not weaken private access controls"
    if http == "404":
        return "P2", "Confirm retired/moved; redirect only to genuinely equivalent content"
    if http and http not in ("200", "403", "404"):
        return "P1", "Investigate unexpected current HTTP response"
    if status == "Conflicting exports":
        return "P2", "Reconcile conflicting exported index states by inspection date"
    if t in ("Feed", "REST API", "Asset"):
        return "Review", "Usually no standalone indexing needed; confirm intent"
    if status == "Crawled – currently not indexed":
        if t in ("Article / page", "Knowledge Base", "Research Hub"):
            return "P2", "Inspect indexed status; compare matched editorial peers and evidence"
        return "Review", "Decide whether archive or nested URL merits indexing"
    if status == "Historical 403":
        return "P3", "Recheck Google validation status; HTTP evidence is not Googlebot proof"
    return "Monitor", "No automated remediation recommended"


def slug_pairs(records: list[dict]) -> list[dict]:
    indexed = [r for r in records if r["indexing_status"] == "Indexed" and r["type"] == "Article / page"]
    excluded = [r for r in records if r["indexing_status"] == "Crawled – currently not indexed" and r["type"] == "Article / page"]
    result = []
    for a in excluded:
        a_slug = urlsplit(a["url"]).path.strip("/")
        a_words = set(a_slug.split("-"))
        match = None
        for b in indexed:
            b_slug = urlsplit(b["url"]).path.strip("/")
            if len(a_words.intersection(b_slug.split("-"))) < 3:
                continue
            similarity = SequenceMatcher(None, a_slug, b_slug).ratio()
            if similarity >= .79 and (match is None or similarity > match[0]):
                match = (similarity, b["url"])
        if match:
            result.append({"unindexed_url": a["url"], "indexed_candidate": match[1],
                           "slug_similarity": round(match[0], 3),
                           "note": "Candidate only: verify actual redirect/canonical and content equivalence"})
    return sorted(result, key=lambda r: -r["slug_similarity"])[:30]


def read_sources(folder: Path, http_tsv: Path | None, evidence=None) -> dict:
    sources = {}
    warnings = []
    for file in sorted(folder.glob("*.xlsx")):
        if file.name.startswith("SC-SEO-"):
            continue
        try:
            raw = source_bytes(file) if evidence else None
            sheets = excel_rows(io.BytesIO(raw) if raw is not None else file)
            kind = source_type(sheets)
            if kind:
                if kind in sources:
                    warnings.append("Multiple " + kind + " workbooks; using last by filename")
                sources[kind] = (file.name, sheets)
                if evidence:
                    digest = evidence.artifact("gsc_export", file, raw)
                    for ordinal, item in enumerate(sheets.get("Table", [])):
                        last_crawl = item.get("Last crawled")
                        evidence.add("gsc_export", digest, ordinal, item.get("URL"),
                                     {"export_classification": kind},
                                     str(last_crawl) if last_crawl not in ("", None) else None)
        except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            warnings.append("Skipped " + file.name + ": " + str(exc))
    if http_tsv is None:
        for candidate in (folder / "SC-SEO-01-live-check.tsv",
                          folder / "SC-SEO-01-403" / "SC-SEO-01-live-check.tsv"):
            if candidate.is_file():
                http_tsv = candidate
                break
    by_url = {}
    inventory_url = (lambda value: url_key(value, evidence.scope) or "") if evidence else normalize

    def add(url, status, crawl):
        row = by_url.get(url)
        if row is None:
            by_url[url] = {"url": url, "indexing_status": status, "last_crawl": crawl,
                           "http_status": "", "final_url": "", "redirect_count": ""}
        elif status in ("Indexed", "Crawled – currently not indexed") and row["indexing_status"] != status:
            if row["indexing_status"] in ("Indexed", "Crawled – currently not indexed"):
                row["indexing_status"] = "Conflicting exports"
        elif not row["last_crawl"]:
            row["last_crawl"] = crawl
    for kind, label in (("indexed", "Indexed"), ("unindexed", "Crawled – currently not indexed"),
                        ("403", "Historical 403")):
        if kind not in sources:
            continue
        for item in sources[kind][1].get("Table", []):
            url = inventory_url(item.get("URL"))
            if url:
                add(url, label, date_value(item.get("Last crawled")))
    http_records = []
    if http_tsv and http_tsv.is_file():
        raw = source_bytes(http_tsv) if evidence else None
        digest = evidence.artifact("historical_http", http_tsv, raw) if evidence else None
        stream = (io.StringIO(raw.decode("utf-8-sig"), newline="") if raw is not None
                  else http_tsv.open("r", encoding="utf-8-sig", newline=""))
        with stream:
            for ordinal, item in enumerate(csv.DictReader(stream, delimiter="\t")):
                if evidence:
                    code = str(item.get("http_status") or "").strip()
                    if re.fullmatch(r"[1-5][0-9]{2}", code):
                        final = item.get("final_url")
                        evidence.add("historical_http", digest, ordinal, item.get("url"),
                                     {"http_status": int(code),
                                      "final_url": final if url_key(final, evidence.scope) else None},
                                     item.get("observed_at") or None)
                    else:
                        evidence.rejected["historical_http"] += 1
                url = inventory_url(item.get("url"))
                if not url:
                    continue
                add(url, "HTTP audit only", "")
                code = str(item.get("http_status") or "").strip()
                row = by_url[url]
                row.update(http_status=code,
                           final_url=inventory_url(item.get("final_url")),
                           redirect_count=str(item.get("redirect_count") or ""))
                http_records.append(row.copy())
    for row in by_url.values():
        row["type"] = page_type(row["url"])
        row["priority"], row["action"] = action_for(row)
    records = sorted(by_url.values(), key=lambda r: (
        {"P1": 0, "P2": 1, "P3": 2, "Review": 3, "Monitor": 4}[r["priority"]],
        r["type"], r["url"]))
    coverage = sources.get("coverage", ("", {}))[1]
    issues = {}
    for item in coverage.get("Critical issues", []):
        try:
            issues[str(item.get("Reason") or "")] = int(item.get("Pages") or 0)
        except (ValueError, TypeError):
            continue
    def latest_count(sheet, key):
        for row in reversed(sheet):
            try:
                if row.get(key) not in (None, ""):
                    return int(row[key])
            except (ValueError, TypeError):
                pass
        return None
    indexed_count = latest_count(sources.get("indexed", ("", {}))[1].get("Chart", []), "Affected pages")
    unindexed_count = latest_count(sources.get("unindexed", ("", {}))[1].get("Chart", []), "Affected pages")
    if indexed_count is None:
        indexed_count = latest_count(coverage.get("Chart", []), "Indexed")
    if unindexed_count is None:
        unindexed_count = issues.get("Crawled - currently not indexed")
    histories = defaultdict(dict)
    for kind, col in (("indexed", "indexed"), ("unindexed", "crawled_not_indexed")):
        for item in sources.get(kind, ("", {}))[1].get("Chart", []):
            date = date_value(item.get("Date"))
            if date:
                try:
                    histories[date][col] = int(item.get("Affected pages"))
                except (ValueError, TypeError):
                    pass
    for item in coverage.get("Chart", []):
        date = date_value(item.get("Date"))
        if date and "indexed" not in histories[date]:
            try:
                histories[date]["indexed"] = int(item["Indexed"])
            except (KeyError, ValueError, TypeError):
                pass
    counts = Counter(row["http_status"] for row in http_records)
    source_names = {kind: filename for kind, (filename, _) in sources.items()}
    if http_tsv and http_tsv.is_file():
        source_names["http"] = http_tsv.name
    return {
        "version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "property": "https://" + DOMAIN + "/",
        "mode": "Read-only historical export analysis — not a live Googlebot test",
        "sources": source_names, "warnings": warnings,
        "coverage": {"indexed_reported": indexed_count, "crawled_not_indexed_reported": unindexed_count,
                     "indexed_export_count": sum(r["indexing_status"] == "Indexed" for r in records),
                     "unindexed_examples": sum(r["indexing_status"] == "Crawled – currently not indexed" for r in records),
                     "reported_issues": issues},
        "types": {
            "indexed": dict(Counter(r["type"] for r in records if r["indexing_status"] == "Indexed")),
            "unindexed_examples": dict(Counter(r["type"] for r in records if r["indexing_status"] == "Crawled – currently not indexed"))
        },
        "http_audit": {"count": len(http_records), "status_counts": dict(counts),
                       "does_not_prove_googlebot_behavior": True},
        "history": [{"date": d, **histories[d]} for d in sorted(histories) if histories[d]],
        "inventory": records, "near_slug_pairs": slug_pairs(records)
    }


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SC-SEO-02 | SEO Intelligence Studio</title>
<style>
:root{--red:#b00020;--black:#141414;--cream:#faf8f5;--line:#dfdcd7;--muted:#697076;--green:#149954}
*{box-sizing:border-box}body{font:15px/1.5 system-ui,-apple-system,sans-serif;margin:0;background:var(--cream);color:var(--black)}
header{background:var(--black);color:white;padding:28px 5%;border-bottom:5px solid var(--red)}
header p{color:#ccc;margin:4px 0}h1{font-size:30px;margin:3px 0}main{max-width:1280px;margin:auto;padding:28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card,.panel{background:white;border:1px solid var(--line);border-radius:9px;padding:18px}
.metric{font-size:32px;font-weight:750}.subtitle{font-size:13px;color:var(--muted)}
h2{font-size:20px;margin:32px 0 12px}input,select{padding:11px;border:1px solid #aeb3b7;border-radius:5px;max-width:100%}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid var(--line)}
td a{overflow-wrap:anywhere}th{background:#f1f0ed}section{overflow:auto}.note{border-left:3px solid var(--red);padding:10px 14px;background:white}
button{border:1px solid var(--line);padding:10px;background:white;cursor:pointer;border-radius:5px}
.priority{font-weight:700}footer{margin-top:25px;color:var(--muted);font-size:12px}
</style></head><body>
<header><small>SUSTAINABLE CATALYST · RESEARCH OPERATIONS</small><h1>SEO Intelligence Studio</h1>
<p>SC-SEO-02 · Offline Google Search Console evidence review · v0.1.0</p></header>
<main><p class="note">This dashboard is generated locally from exports. Historical Google index classifications
and ordinary HTTP responses are different evidence classes; a 200 response does not guarantee indexing.</p>
<div class="grid" id="metrics"></div><h2>Investigations</h2><div class="panel" id="queue"></div>
<h2>URL inventory</h2><section class="panel"><div class="filters">
<input id="query" placeholder="Search URL or action" aria-label="Search URL inventory">
<select id="kind" aria-label="Filter page type"><option value="">All types</option></select>
<select id="state" aria-label="Filter indexing state"><option value="">All states</option></select>
<select id="priority" aria-label="Filter priority"><option value="">All priorities</option><option>P1</option><option>P2</option><option>P3</option><option>Review</option><option>Monitor</option></select>
</div><div id="count" class="subtitle"></div><table><thead><tr><th>Priority</th><th>URL</th><th>Index state</th><th>Type</th><th>HTTP</th><th>Action</th></tr></thead><tbody id="rows"></tbody></table>
<button id="previous">Previous</button> <button id="next">Next</button></section>
<h2>Indexing trend from Search Console exports</h2><div class="panel" id="history"></div>
<h2>Technical crawl and publication evidence</h2><section class="panel">
<p id="health"></p><div id="validation"></div>
<label>Find URL or publication <input id="evidence-query" type="search"></label>
<label><input id="findings-only" type="checkbox"> Findings only</label>
<div id="investigations"></div><button id="more-evidence" type="button">Show more</button>
</section>
<section id="publication-discovery" hidden><h2>Publication discoverability</h2><div class="panel" id="publication-review"></div></section>
<h2>Evidence and limitations</h2><div class="panel" id="sources"></div>
<footer>No automatic redirects, publishing changes, validation requests, or content quality scores.</footer>
</main><script id="evidence" type="application/json">__DATA__</script>
<script>
const d=JSON.parse(document.getElementById("evidence").textContent);
const el=(tag,text)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);return x;};
const metric=(name,val)=>{const x=el("div");x.className="card";const y=el("div",val??"Not supplied");y.className="metric";x.append(y,el("div",name));document.getElementById("metrics").append(x);};
metric("Indexed (reported)",d.coverage.indexed_reported);
metric("Crawled, not indexed (reported)",d.coverage.crawled_not_indexed_reported);
metric("Unindexed examples imported",d.coverage.unindexed_examples);
metric("Current HTTP 403 in supplied audit",d.http_audit.status_counts["403"]||0);
const history=document.getElementById("history");
if(d.history.length){
const table=el("table"), header=el("tr");
for(const label of ["Date","Indexed","Crawled, currently not indexed"]){const th=el("th",label);th.scope="col";header.append(th);}
const head=el("thead"), body=el("tbody");head.append(header);table.append(head,body);
for(const item of d.history){const tr=el("tr");for(const key of ["date","indexed","crawled_not_indexed"])tr.append(el("td",item[key]??"—"));body.append(tr);}
history.append(table);
}else history.textContent="No dated Search Console coverage observations supplied.";
if(d.publication_discovery){
const p=d.publication_discovery, s=p.summary;
document.getElementById("publication-discovery").hidden=false;
const review=document.getElementById("publication-review");
review.append(el("p",s.fields+" fields · "+s.maps+" Article Maps · "+s.distinct_articles+" article targets"));
review.append(el("p",s.articles_linked_in_initial_main+" article targets linked from the initial main HTML · "+s.parameter_variants+" parameterized hub links"));
review.append(el("p",s.article_targets_without_path_in_captures+" targets without a path in the captured link graph. Sitewide orphan status requires broader evidence."));
const details=el("details");details.append(el("summary","Article link and evidence inventory"));
for(const a of p.articles) details.append(el("p",a.url+" · "+(a.hub_main_anchor?"hub link":"no initial main link")+" · sitemap "+(a.sitemap_member===true?"listed":"unknown")+" · "+(a.google_export_classes.join(", ")||"no Google export observation")));
review.append(details);
if(d.publication_comparison){
const c=d.publication_comparison, before=c.counts_before, after=c.counts_after;
review.append(el("p","Map links "+before.maps_linked_in_initial_main+" → "+after.maps_linked_in_initial_main+
" · Article links "+before.articles_linked_in_initial_main+" → "+after.articles_linked_in_initial_main));
}
}
const evidence=d.evidence_history;
const health=document.getElementById("health"), validation=document.getElementById("validation");
const investigations=document.getElementById("investigations"), more=document.getElementById("more-evidence");
if(evidence){
health.textContent=Object.entries(evidence.crawl_http_counts).map(([code,n])=>code+" · "+n).join(" | ")||"No SiteOne HTTP observations supplied.";
validation.textContent="Imported artifacts · "+evidence.artifacts.length+" | Rejected source rows · "+Object.values(evidence.rejected).reduce((a,b)=>a+b,0);
validation.append(el("p","SiteOne HTTP results and Search Console indexing observations are reported separately."));
const provenance=el("details");provenance.append(el("summary","Artifact and import history"));
for(const a of evidence.artifacts)provenance.append(el("p",[a.source_kind,a.source_file,a.imported_at_utc,a.source_sha256].join(" · ")));
validation.append(provenance);
const search=document.getElementById("evidence-query"), only=document.getElementById("findings-only");
const pages=evidence.pages.slice().sort((a,b)=>(a.findings[0]?.priority??"Z").localeCompare(b.findings[0]?.priority??"Z"));
function appendPages(offset){
const query=search.value.toLowerCase();
const matches=pages.filter(p=>(!only.checked||p.findings.length)&&(p.url.toLowerCase().includes(query)||p.observations.some(o=>String(o.facts.publication_id??"").toLowerCase().includes(query))));
if(!offset)investigations.replaceChildren(el("p",matches.length+" matching URLs"));
for(const page of matches.slice(offset,offset+30)){
const details=el("details");details.append(el("summary",page.url+" · "+page.findings.length+" finding(s)"));
details.append(el("p",page.sitemap_member?"Listed in a supplied XML sitemap":"Sitemap membership unknown"));
for(const f of page.findings.slice().sort((a,b)=>a.priority.localeCompare(b.priority)))details.append(el("p",f.priority+" · "+f.action+" · "+f.observation_id));
for(const o of page.observations){
const facts=Object.entries(o.facts).filter(([,v])=>v!==null).map(([k,v])=>k+" = "+v);
details.append(el("p",[o.source_kind,o.source_time_raw??"source time unknown",...facts,"SHA-256 "+o.source_sha256,"Observation "+o.observation_id].join(" · ")));
}
investigations.append(details);
}
more.hidden=offset+30>=matches.length;
more.onclick=()=>appendPages(offset+30);
}
search.oninput=only.onchange=()=>appendPages(0);appendPages(0);
}else{health.textContent="No unified evidence supplied.";more.hidden=true;}
const counts={};d.inventory.forEach(x=>{if(x.priority!=="Monitor"){const k=x.priority+" · "+x.action;counts[k]=(counts[k]||0)+1;}});
const q=document.getElementById("queue");Object.entries(counts).sort().forEach(([k,n])=>{q.append(el("p",n+" URL(s): "+k));});
const unique=k=>[...new Set(d.inventory.map(r=>r[k]))].sort();
for(const [id,key] of [["kind","type"],["state","indexing_status"]]){for(const value of unique(key)){const option=el("option",value);option.value=value;document.getElementById(id).append(option);}}
let filtered=[],page=0;
function render(){
const query=document.getElementById("query").value.toLowerCase(),kind=document.getElementById("kind").value,state=document.getElementById("state").value,priority=document.getElementById("priority").value;
filtered=d.inventory.filter(r=>(!query||(r.url+" "+r.action).toLowerCase().includes(query))&&(!kind||r.type===kind)&&(!state||r.indexing_status===state)&&(!priority||r.priority===priority));
const max=Math.max(0,Math.ceil(filtered.length/35)-1);page=Math.min(page,max);
const body=document.getElementById("rows");body.replaceChildren();
for(const r of filtered.slice(page*35,page*35+35)){const tr=el("tr");tr.append(el("td",r.priority));const cell=el("td");const a=el("a",r.url);a.href=r.url;a.rel="noopener noreferrer";a.target="_blank";cell.append(a);tr.append(cell);
for(const field of ["indexing_status","type","http_status","action"])tr.append(el("td",r[field]||"—"));body.append(tr);}
document.getElementById("count").textContent=filtered.length+" matching URLs; page "+(page+1)+" of "+(max+1);
document.getElementById("previous").disabled=page===0;document.getElementById("next").disabled=page>=max;
}
for(const id of ["query","kind","state","priority"])document.getElementById(id).addEventListener("input",()=>{page=0;render();});
document.getElementById("previous").onclick=()=>{page=Math.max(0,page-1);render();};
document.getElementById("next").onclick=()=>{page++;render();};render();
document.getElementById("sources").textContent="Imported: "+Object.entries(d.sources).map(([k,v])=>k+": "+v).join(" · ")+" | All data stays in this local HTML file; do not publish generated reports.";
</script></body></html>"""


def dashboard(report: dict) -> str:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for char, replacement in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e")):
        payload = payload.replace(char, replacement)
    return HTML.replace("__DATA__", payload)


def write_csv(path: Path, records: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: csv_safe(row.get(key)) for key in fields} for row in records)


def save_report(report: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "seo-dashboard.html").write_text(dashboard(report), encoding="utf-8")
    (output / "seo-summary.json").write_text(json.dumps(
        {k: v for k, v in report.items() if k != "inventory"}, ensure_ascii=False, indent=2), encoding="utf-8")
    if "publication_discovery" in report:
        (output / "seo-publication-discovery.json").write_text(
            json.dumps(report["publication_discovery"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if "publication_comparison" in report:
        (output / "seo-publication-comparison.json").write_text(
            json.dumps(report["publication_comparison"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(output / "seo-url-inventory.csv", report["inventory"],
              ["priority", "url", "indexing_status", "type", "last_crawl",
               "http_status", "redirect_count", "final_url", "action"])
    counts = defaultdict(list)
    for record in report["inventory"]:
        if record["priority"] != "Monitor":
            counts[(record["priority"], record["action"])].append(record["url"])
    queue = [
        {"priority": key[0], "action": key[1], "url_count_in_imports": len(urls),
         "example_url": urls[0], "evidence_scope": "Export sample / ordinary HTTP only; review required"}
        for key, urls in sorted(counts.items())
    ]
    write_csv(output / "seo-priority-queue.csv", queue,
              ["priority", "action", "url_count_in_imports", "example_url", "evidence_scope"])


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description="Read-only SC-SEO-02 export analysis")
    cli.add_argument("--input-dir", required=True, type=Path, help="Directory containing original Google .xlsx exports")
    cli.add_argument("--output-dir", required=True, type=Path, help="Local private output directory")
    cli.add_argument("--http-tsv", type=Path, default=None, help="Optional SC-SEO-01 HTTP audit TSV")
    cli.add_argument("--siteone-json", type=Path, action="append", default=[])
    cli.add_argument("--sitemap-xml", type=Path, action="append", default=[])
    cli.add_argument("--html-response", nargs=2, metavar=("URL", "FILE"), action="append", default=[],
                     help="Trusted local capture of a URL's HTML response body")
    cli.add_argument("--publication-baseline", type=Path, help="Local discovery report to compare against")
    cli.add_argument("--publication-hub-html", type=Path, help="Captured initial HTML for /publications/")
    cli.add_argument("--publication-rendered-html", nargs=2, metavar=("URL", "FILE"), action="append", default=[])
    cli.add_argument("--publications-json", type=Path, help="Explicitly authorized local publication metadata")
    cli.add_argument("--property", default="https://" + DOMAIN + "/")
    cli.add_argument("--imported-at", help="Explicit UTC import time for reproducible reports")
    args = cli.parse_args(argv)
    folder = args.input_dir.expanduser().resolve()
    if not folder.is_dir():
        cli.error("Input directory does not exist")
    try:
        scope = property_origin(args.property)
        evidence = EvidenceHistory(scope, args.imported_at)
        report = read_sources(folder, args.http_tsv, evidence)
        for path in args.siteone_json:
            evidence.siteone(parse_report(path, args.property, evidence.imported_at))
        for path in args.sitemap_xml:
            evidence.sitemap(path)
        for url, path in args.html_response:
            evidence.html_response(url, Path(path))
        if args.publication_hub_html:
            evidence.html_response(scope.rstrip("/") + "/publications/", args.publication_hub_html)
        if args.publications_json:
            evidence.publications(args.publications_json)
    except (ValueError, OSError, ET.ParseError, csv.Error) as exc:
        cli.error(str(exc))
    if not report["sources"] and not evidence.artifacts:
        cli.error("No supported source exports found")
    report["generated_at"] = evidence.imported_at
    report["property"] = scope
    report["evidence_history"] = evidence.build()
    if args.publication_hub_html:
        try:
            report["publication_discovery"] = analyze_publications(
                [(scope.rstrip("/") + "/publications/", args.publication_hub_html),
                 *((url, Path(path)) for url, path in args.html_response)],
                [(url, Path(path)) for url, path in args.publication_rendered_html],
                report["evidence_history"])
            if args.publication_baseline:
                baseline = json.loads(source_bytes(args.publication_baseline))
                report["publication_comparison"] = compare_publication_discovery(
                    baseline, report["publication_discovery"])
        except (ValueError, OSError) as exc:
            cli.error(str(exc))
    save_report(report, args.output_dir.expanduser().resolve())
    print("SC-SEO-02 v" + VERSION + " — offline report generated")
    print("Sources:", ", ".join(report["sources"].keys()))
    print("Unique URLs:", len(report["inventory"]))
    print("Indexed:", report["coverage"]["indexed_reported"])
    print("Crawled not indexed:", report["coverage"]["crawled_not_indexed_reported"])
    print("HTTP statuses:", report["http_audit"]["status_counts"])
    print("Dashboard:", args.output_dir / "seo-dashboard.html")
    for warning in report["warnings"]:
        print("Warning:", warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
