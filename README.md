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

Requires Python 3.10 or newer. Store the actual Search Console exports in a local folder such as Downloads, not inside this public Git checkout.

~~~bash
git clone --branch feature/sc-seo-02-initial-studio https://github.com/Content-Catalyst-LLC/sustainable-catalyst-seo-intelligence.git
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

## SC-SEO-02.1 — SiteOne adapter (draft PR #3)

Read-only, offline ingestion of a SiteOne JSON report into the versioned URL-observation evidence contract. It **does not run SiteOne, crawl a site, authenticate to Google, or modify WordPress**. For upstream release pin, observed JSON fields, source-time caveats, and acceptance gates, see [docs/SITEONE-ADAPTER.md](docs/SITEONE-ADAPTER.md).

~~~bash
python3 seo_siteone.py \\
  --siteone-json fixtures/siteone-synthetic-v2.json \\
  --property https://example.test/ \\
  --imported-at 2026-11-20T12:31:00Z \\
  --output-dir /tmp/sc-seo-02-siteone-synthetic

python3 -m unittest discover -s tests -v
~~~

With an independently approved real SiteOne report, set `--siteone-json` to the private JSON file, `--property` to the exact authorized HTTPS origin, `--output-dir` to a **private path outside Git**, and optionally `--studio-inventory` to the existing v0.1 `seo-url-inventory.csv`. Outputs include the immutable source digest, provenance receipt, normalized JSON observations and optional reconciliation CSV. Do not publish owner-specific outputs.

**SiteOne v2.5.1 passed isolated loopback acceptance.** Hosted CI uses synthetic inputs and a projection captured from the verified crawler. See [SiteOne acceptance](docs/SITEONE-ACCEPTANCE.md).

## Approved hybrid build map (2026-11-20)

See [BUILD_MAP.md](BUILD_MAP.md). Next: **SC-SEO-02.1** imports version-pinned SiteOne crawl JSON, establishes a typed evidence/provenance contract, and evaluates CrawlSEO versus a small official GSC read-only adapter in isolated staging. SiteOne and CrawlSEO are **not yet installed or integrated**. Following milestones: **02.2** authenticated Workspace/Publication Studio, **02.3** publication quality, **02.4** recovery/monitoring, **02.5** optional competitive intelligence.

**Current release gate: PROTOTYPE — production NO-GO.** No automated Google validation, content changes, redirects, bulk publishing, access-control changes, or account mutations are supported.

## Google references

- [Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index)
- [URL Inspection API](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [Search Console page indexing](https://support.google.com/webmasters/answer/7440203)

## SC-SEO-02.1C/D · Technical evidence in Studio

The existing dashboard can import SiteOne JSON, XML sitemap documents, captured HTML bodies and authorized local publication metadata alongside Google exports and HTTP audit TSVs. See [the evidence contract and inputs](docs/EVIDENCE-HISTORY.md), [SiteOne acceptance](docs/SITEONE-ACCEPTANCE.md) and [the CrawlSEO decision](docs/CRAWLSEO-DECISION.md).
