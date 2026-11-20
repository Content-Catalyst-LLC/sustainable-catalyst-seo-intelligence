# Sustainable Catalyst — SEO Intelligence Studio

**SC-SEO-02 v0.1.0** · Public source code; private operational evidence

An evidence-first SEO application for Sustainable Catalyst's research, publishing, and knowledge infrastructure. It reconciles Google Search Console exports with optional independent HTTP checks, produces a read-only indexing dashboard, and prioritizes investigation without pretending to know Google's private ranking or quality systems.

## Implemented in v0.1.0

- Imports native Search Console Excel (.xlsx) exports for coverage, indexed pages, crawled-but-unindexed pages, and 403 errors.
- Imports an optional ordinary HTTP audit TSV from SC-SEO-01.
- Keeps historical Search Console status separate from current ordinary HTTP responses.
- Classifies articles, category archives, feeds, API endpoints, Knowledge Base, Research Hub, and application routes.
- Generates a **local HTML dashboard**, URL inventory CSV, issue queue CSV, and summary JSON.
- Flags similar-slug pairs for human review rather than automatically redirecting them.
- Has no required third-party dependencies, Google credentials, network access, or WordPress writes.

This is an **offline prototype**; it is not deployed and does not perform a fresh Googlebot live inspection.

## Run on Mac

Requires Python 3.9 or newer. Store the actual Search Console exports in a local folder such as Downloads, not inside this public Git checkout.

~~~bash
git clone https://github.com/Content-Catalyst-LLC/sustainable-catalyst-seo-intelligence.git
cd sustainable-catalyst-seo-intelligence
python3 seo_studio.py --input-dir ~/Downloads \
  --output-dir ~/Downloads/SC-SEO-02-private-report \
  --http-tsv ~/Downloads/SC-SEO-01-403/SC-SEO-01-live-check.tsv
open ~/Downloads/SC-SEO-02-private-report/seo-dashboard.html
~~~

Omit the optional http-tsv flag if unavailable. The tool also searches for SC-SEO-01-live-check.tsv in the import directory and its SC-SEO-01-403 subdirectory.

Run tests:

~~~bash
python3 -m unittest discover -s tests -v
python3 -m py_compile seo_studio.py
~~~

## Generated reports (keep private)

| Output | Purpose |
| --- | --- |
| seo-dashboard.html | Filterable local investigation dashboard; includes embedded URL-level evidence |
| seo-url-inventory.csv | Joined URL status, type, historical crawl date, current HTTP status and next action |
| seo-priority-queue.csv | Grouped investigation tasks, sample counts and examples |
| seo-summary.json | Trend and issue metadata, reported vs sampled totals, similar-slug candidates |

**Never commit or publicly serve the generated reports from real data.** The HTML file embeds the imported URL-level information.

## Evidence rules

- An ordinary HTTP 200 is not proof of Googlebot access or indexing.
- Google's historical Page Indexing classifications may lag behind current server behavior.
- A 1,000-row unindexed export is typically a *sample*, not the full excluded population.
- An indexing-eligible page can still be declined by Google's indexing selection process.
- Similar slugs do not prove duplicate content; redirect/canonical checks must be grounded in actual behavior.
- A publication-quality judgment must be editorially reviewed; this tool never fabricates a Google quality score.
- The URL Inspection API exposes Google's *indexed-version* information, not a live-URL test.

## Public/private boundary

- This **public repository**: source code, tests, documentation, and synthetic fixtures.
- Private [Content-Catalyst-LLC/data](https://github.com/Content-Catalyst-LLC/data): approved evidence and manifests under evidence/seo/.
- Private [Content-Catalyst-LLC/documentation](https://github.com/Content-Catalyst-LLC/documentation): architecture and release/build map.
- Private [Content-Catalyst-LLC/handoffs](https://github.com/Content-Catalyst-LLC/handoffs): active handoff, owner decisions, and blockers.

A folder named data/ in a public repository is NOT private. Our .gitignore prevents accidental commits but cannot remove secrets already committed. Never commit credentials, access tokens, private keys, personal information, or unapproved licensed data even to a private repository; use a secrets manager for credentials.

## Proposed later phases

**SC-SEO-02.1:** Authenticated Workspace / Publication Studio integration and durable provenance receipts.

**SC-SEO-02.2:** Authorized, quota-aware Search Console API and WordPress read APIs; export-import fallback retained.

**SC-SEO-02.3:** Matched indexed/unindexed publication quality reviews, original research evidence and knowledge graph context.

**SC-SEO-02.4:** Approved remediation and recovery monitoring with explicit owner signoff, tests and rollback gates.

**Current release gate: PROTOTYPE — production NO-GO.** No automated Google validation, content changes, redirects, bulk publishing, access-control changes, or account mutations are supported.

## Google references

- [Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index)
- [URL Inspection API](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [Search Console page indexing](https://support.google.com/webmasters/answer/7440203)
