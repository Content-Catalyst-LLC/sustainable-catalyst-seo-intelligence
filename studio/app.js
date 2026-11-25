"use strict";
// SEO Studio client. Holds no data of its own: every view is fetched from /api with a Workspace-signed session.
(() => {
  const SESSION_URL = document.body.dataset.sessionUrl;
  const view = document.getElementById("view");
  const runSelect = document.getElementById("run");
  let session = null, sessionUntil = 0, runs = [], tab = "overview", cache = {};
  const applied = {filter: "", q: "", kind: ""}; // last filters applied in Pages / Recommendations (used by "Current filters" export)

  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const num = (v) => (typeof v === "number" ? v.toLocaleString() : esc(v));
  const card = (n, l, cls = "") => `<div class="card"><div class="n ${cls}">${num(n)}</div><div class="l">${esc(l)}</div></div>`;
  const tag = (cls) => `<span class="tag ${cls}">${cls}</span>`;
  const table = (cols, rows) => rows && rows.length
    ? `<table><thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map((r) =>
      `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    : `<p class="note">No rows.</p>`;
  const kv = (obj) => table([["Value", (r) => esc(r[0])], ["Count", (r) => num(r[1])]], Object.entries(obj || {}).sort((a, b) => b[1] - a[1]));
  const note = (t, gap = false) => `<p class="note${gap ? " gap" : ""}">${esc(t)}</p>`;
  const link = (u) => `<a class="u" data-url="${esc(u)}">${esc(u)}</a>`;

  async function getSession() {
    if (session && Date.now() < sessionUntil) return session;
    // Workspace issues a short-lived signed SEO session for the signed-in person (cookie-authenticated, same as Research).
    const r = await fetch(SESSION_URL, {method: "POST", credentials: "include", headers: {"Accept": "application/json"}});
    if (!r.ok) throw new Error(r.status === 401 ? "Sign in to Sustainable Catalyst, then reload." : "Workspace could not issue an SEO Studio session.");
    const doc = await r.json(); // the signed sc-workspace-seo-session/1.0 document, as Workspace returns it
    const bytes = new TextEncoder().encode(JSON.stringify(doc));
    let bin = ""; bytes.forEach((b) => { bin += String.fromCharCode(b); });
    session = "SC-Workspace " + btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const life = (Date.parse(doc.expiresAt) - Date.parse(doc.issuedAt)) || 120000;
    sessionUntil = Date.now() + Math.max(30000, life - 60000);
    return session;
  }

  async function api(path) {
    const key = path;
    if (cache[key]) return cache[key];
    const r = await fetch(path, {headers: {Authorization: await getSession()}});
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || "Request failed");
    return (cache[key] = body);
  }

  const run = () => runSelect.value;
  const q = (o) => new URLSearchParams(o).toString();

  async function render() {
    view.innerHTML = "<p>Loading…</p>";
    try { view.innerHTML = await VIEWS[tab](); } catch (e) { view.innerHTML = note(e.message, true); }
  }

  async function overview() { return api("/api/overview?" + q({run: run()})); }

  const VIEWS = {
    async overview() {
      const o = await overview(), t = o.technical, w = o.observed_window || {};
      const rec = o.provenance.reconciliation || [];
      return `<div class="grid">
        ${card(Object.values(t.status).reduce((a, b) => a + b, 0), "URLs with HTTP observations")}
        ${card(t.status["200"] || 0, "HTTP 200")}
        ${card(o.broken_links.edges, "Broken internal link edges")}
        ${card((o.sitemaps.summary || {}).unique_urls ?? "—", "Sitemap URLs")}
        ${card(o.metadata.html_200_without_description, "200 pages without description")}
        ${card(o.indexing.pages_with_google_evidence, "Pages with Google evidence")}
      </div>
      <h2>Run</h2>${table([["Field", (r) => esc(r[0])], ["Value", (r) => esc(r[1])]], [
        ["Label", o.run.label], ["Kind", o.run.kind], ["Imported", o.run.imported_at_utc], ["History SHA-256", o.run.history_sha256],
        ["Baseline", o.provenance.baseline_id], ["Observed window", `${w.start || "start not recorded"} → ${w.end || "—"} (precision: ${w.precision || "—"})`],
        ["Reconciliation", rec.length ? `${rec.filter((r) => r.match).length}/${rec.length} checks match` : "—"]])}
      <h2>Release batches</h2>${table([["Batch", (r) => esc(r.batch)], ["State", (r) => esc(r.state)], ["Scope", (r) => esc(r.scope)]], o.change_events)}
      ${note("Batch states come from a dated production observation. A batch is shown as deployed only with verified production evidence.")}
      <h2>Inputs</h2>${table([["Kind", (r) => esc(r.source_kind)], ["File", (r) => esc(r.source_file)], ["SHA-256", (r) => `<code>${esc(r.source_sha256.slice(0, 16))}…</code>`]], o.provenance.artifacts)}`;
    },
    async technical() {
      const o = await overview(), t = o.technical;
      return `<h2>HTTP status ${tag("measured")}</h2>${kv(t.status)}
        <div class="grid">${card(t.redirected, "Redirected URLs")}${card(t.canonical_not_self, "Canonical not self")}${card(t.noindex, "noindex (200 pages)")}
        ${card(t.ttfb_ms ? t.ttfb_ms.median : "—", "Median TTFB (ms)")}${card(t.ttfb_ms ? t.ttfb_ms.p95 : "—", "p95 TTFB (ms)")}</div>
        <h2>Cache status (200 pages)</h2>${kv(t.cache)}
        <h2>Findings ${tag("measured")}</h2>${table([["Priority", (r) => `<span class="${r.priority}">${r.priority}</span>`], ["Action", (r) => esc(r.action)], ["Pages", (r) => num(r.n)]], t.measured_findings)}`;
    },
    async indexing() {
      const o = await overview(), i = o.indexing;
      return (i.note ? note(i.note, true) : "") +
        `<h2>Google coverage (URL Inspection) ${tag("measured")}</h2>${kv(i.coverage)}
         <h2>Search performance</h2>${table([["Engine", (r) => esc(r.engine)], ["Pages", (r) => num(r.pages)], ["Clicks", (r) => num(r.clicks)], ["Impressions", (r) => num(r.impressions)]], i.search_performance)}
         ${note("Indexed/not indexed is shown only from search-engine evidence. HTTP status and sitemap membership are not treated as indexing.")}`;
    },
    async metadata() {
      const m = (await overview()).metadata;
      return `<div class="grid">${card(m.html_200_without_description, "200 pages without description")}${card(m.multiple_h1, "Multiple H1")}${card(m.no_h1, "No H1")}</div>
        <h2>Prior audit classifications ${tag("prior")}</h2>${table([["Priority", (r) => `<span class="${r.priority}">${r.priority}</span>`], ["Classification", (r) => esc(r.action)], ["URLs", (r) => num(r.n)]], m.prior_classifications)}
        ${note("Prior classifications are imported verbatim from the earlier audit and are not recomputed.")}`;
    },
    async links() {
      const b = (await overview()).broken_links;
      return `<div class="grid">${card(b.edges, "Broken internal link edges")}${card(b.top_targets.length, "Targets shown")}</div>
        <h2>Most-linked failing targets ${tag("measured")}</h2>${table([["Target", (r) => link(r.target)], ["Internal links", (r) => num(r.inlinks)]], b.top_targets)}`;
    },
    async sitemaps() {
      const s = (await overview()).sitemaps, sum = s.summary || {};
      return `<div class="grid">${card(sum.unique_urls ?? "—", "Sitemap URLs")}${card(`${sum.loaded ?? "—"}/${sum.declared ?? "—"}`, "Leaves loaded / declared")}
        ${card(s.members_not_200, "Members not HTTP 200")}${card(s.members_noindex, "Members with noindex")}</div>
        <h2>Published in WordPress, not in a sitemap</h2>${kv(s.published_not_in_sitemap)}
        ${note("Sitemap eligibility follows the content's own governance (e.g. Knowledge Base verification); this view does not promote content.")}`;
    },
    async structured() {
      return `<h2>JSON-LD validation ${tag("measured")}</h2>${kv((await overview()).structured_data)}
        ${note("ok = parses and every @id reference resolves · unresolved = dangling @id · invalid = does not parse · none = no JSON-LD.")}`;
    },
    async publications() {
      const p = (await overview()).publications;
      return p.gap ? note(p.gap, true) : `<h2>Publications discovery</h2><pre>${esc(JSON.stringify(p.summary, null, 2))}</pre>`;
    },
    async kb() {
      const k = (await overview()).knowledge_base;
      return `<h2>Recorded verification states</h2>${kv(k.verification_states)}<h2>In a sitemap, by state</h2>${kv(k.in_sitemap_by_state)}${note(k.note)}`;
    },
    async pages() {
      // Filters come from state set by Apply: render() replaces the view (and these inputs) before this runs.
      const filter = applied.filter, text = applied.q, offset = +(view.dataset.offset || 0);
      const r = await api("/api/pages?" + q({run: run(), filter, q: text, offset}));
      const opts = ["", "findings", "broken_out", "not_200", "no_description", "multiple_h1", "sitemap_problem", "kb_unverified", "structured_data"];
      return `<div class="toolbar"><input id="s" placeholder="URL contains" value="${esc(text)}"><select id="f">${opts.map((o) => `<option ${o === filter ? "selected" : ""} value="${o}">${o || "all pages"}</option>`).join("")}</select>
        <button id="go">Apply</button><span class="note">${num(r.total)} pages · ${r.offset + 1}–${Math.min(r.offset + 50, r.total)}</span>
        <button id="prev" ${r.offset ? "" : "disabled"}>Previous</button><button id="next" ${r.offset + 50 < r.total ? "" : "disabled"}>Next</button></div>
        ${table([["URL", (x) => link(x.url)], ["Status", (x) => esc(x.http_status)], ["Sitemap", (x) => (x.sitemap_member ? "yes" : "—")], ["WP", (x) => esc(x.wordpress_status)],
          ["Verification", (x) => esc(x.verification_state)], ["Measured", (x) => num(x.measured_findings)], ["Prior", (x) => num(x.prior_findings)], ["Broken out", (x) => num(x.broken_links_out)]], r.rows)}`;
    },
    async recommendations() {
      const offset = +(view.dataset.offset || 0), kind = view.dataset.kind || "";
      applied.kind = kind;
      const [o, r] = await Promise.all([overview(), api("/api/recommendations?" + q({run: run(), kind, offset}))]);
      return `${note("Inferred opportunities. Each needs human approval and changes nothing; an approved change ships only as an authorized remediation batch.")}
        <div class="toolbar">${["", ...o.recommendations.map((x) => x.kind)].map((k) => `<button class="kind" data-kind="${esc(k)}">${esc(k || "all")}${k ? ` (${o.recommendations.find((x) => x.kind === k).n})` : ""}</button>`).join("")}
        <span class="note">${num(r.total)} · ${r.offset + 1}–${Math.min(r.offset + 50, r.total)}</span><button id="prev" ${r.offset ? "" : "disabled"}>Previous</button><button id="next" ${r.offset + 50 < r.total ? "" : "disabled"}>Next</button></div>
        ${table([["Kind", (x) => `${tag("inferred")} ${esc(x.kind)}`], ["Subject", (x) => (x.subject.startsWith("http") ? link(x.subject) : esc(x.subject))], ["Summary", (x) => esc(x.summary)], ["Status", (x) => esc(x.status)]], r.rows)}`;
    },
    async history() {
      if (runs.length < 2) return note("One run stored. Comparison appears once a second run (a verification pass or connector pull) is stored.");
      const [a, b] = [runs[runs.length - 2].run_id, runs[runs.length - 1].run_id];
      const c = await api("/api/compare?" + q({before: a, after: b}));
      return `<h2>${esc(a)} → ${esc(b)}</h2><div class="grid">${card(c.urls_observed_in_both, "Observed in both")}${card(c.status_changed_count, "Status changed")}${card(c.only_in_before, "Only in earlier")}${card(c.only_in_after, "Only in later")}</div>
        ${table([["URL", (r) => link(r.url)], ["Before", (r) => esc(r.before_status)], ["After", (r) => esc(r.after_status)]], c.status_changed)}${note(c.note)}`;
    },
  };

  async function showPage(url) {
    const d = await api("/api/page?" + q({run: run(), url}));
    document.getElementById("detail-body").innerHTML = `<h2>${esc(url)}</h2>
      <h3>Findings</h3>${table([["Class", (r) => tag(r.evidence_class)], ["Priority", (r) => `<span class="${r.priority}">${r.priority}</span>`], ["Action", (r) => esc(r.action)], ["Origin", (r) => esc(r.origin)]], d.findings)}
      <h3>Recommendations</h3>${table([["Kind", (r) => esc(r.kind)], ["Summary", (r) => esc(r.summary)]], d.recommendations)}
      <h3>Observations</h3>${d.observations.map((o) => `<details><summary>${esc(o.source_kind)} · ${esc(o.source_sha256.slice(0, 12))}… #${esc(o.source_record)} · time ${esc(o.source_time_raw || "not recorded per row")}</summary><pre>${esc(JSON.stringify(o.facts, null, 2))}</pre></details>`).join("")}`;
    document.getElementById("detail").showModal();
  }

  document.getElementById("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    document.querySelectorAll("#tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    tab = b.dataset.tab; view.dataset.offset = 0; view.dataset.kind = ""; render();
  });
  view.addEventListener("click", (e) => {
    const t = e.target;
    if (t.dataset.url) return showPage(t.dataset.url).catch((err) => alertNote(err));
    if (t.id === "go") {
      applied.filter = document.getElementById("f").value; applied.q = document.getElementById("s").value;
      view.dataset.offset = 0; return render();
    }
    if (t.id === "next" || t.id === "prev") { view.dataset.offset = Math.max(0, +(view.dataset.offset || 0) + (t.id === "next" ? 50 : -50)); return render(); }
    if (t.classList.contains("kind")) { view.dataset.kind = t.dataset.kind; view.dataset.offset = 0; return render(); }
  });
  const alertNote = (err) => { view.insertAdjacentHTML("afterbegin", note(err.message, true)); };
  document.getElementById("close").addEventListener("click", () => document.getElementById("detail").close());
  runSelect.addEventListener("change", () => { cache = {}; render(); });

  // Export to Excel: the server builds the workbook from the same read-only store, under the same session check.
  const exportStatus = document.getElementById("export-status");
  const describe = () => [applied.filter && `filter ${applied.filter}`, applied.q && `URL contains “${applied.q}”`,
    applied.kind && `recommendations: ${applied.kind}`].filter(Boolean).join("; ") || "no filters applied";
  document.getElementById("export-scope").addEventListener("change", (e) => {
    exportStatus.textContent = e.target.value === "filtered" ? "Current filters: " + describe() : "";
  });
  document.getElementById("export").addEventListener("click", async (e) => {
    const button = e.currentTarget, scope = document.getElementById("export-scope").value;
    if (!run()) return;
    button.disabled = true;
    exportStatus.textContent = "Preparing workbook…";
    try {
      const params = {run: run(), scope};
      if (scope === "filtered") Object.assign(params, {filter: applied.filter, q: applied.q, kind: applied.kind});
      const r = await fetch("/api/export.xlsx?" + q(params), {headers: {Authorization: await getSession()}});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Export failed");
      const name = (/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "") || [])[1] || "seo-studio-export.xlsx";
      const href = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), {href, download: name});
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 30000);
      exportStatus.textContent = "Downloaded " + name + (scope === "filtered" ? " (" + describe() + ")" : " (entire run)");
    } catch (err) {
      exportStatus.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });

  (async () => {
    try {
      const r = await api("/api/runs");
      runs = r.runs;
      document.getElementById("who").textContent = "Signed in as " + r.subject;
      runSelect.innerHTML = runs.map((x) => `<option value="${esc(x.run_id)}">${esc(x.label)} (${esc(x.imported_at_utc)})</option>`).join("");
      runSelect.value = runs.length ? runs[runs.length - 1].run_id : "";
      if (!runs.length) { view.innerHTML = note("No runs stored yet.", true); return; }
      render();
    } catch (e) { view.innerHTML = note(e.message, true); }
  })();
})();
