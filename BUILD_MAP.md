# SC-SEO-02 — SEO Intelligence Studio build map

**Updated:** 2026-11-20 · **Status:** design approved, implementation staged · **Production:** NO-GO

## Product decision

Build the *Sustainable Catalyst SEO Intelligence layer* ourselves: normalize indexing/crawl evidence, tie technical findings to publications, and provide transparent review and recovery workflows. **Reuse** external open-source tools for generic website crawling and, only if justified, Google Search Console data collection.

| Component | Direction | Scope |
| --- | --- | --- |
| [SiteOne Crawler](https://github.com/janreges/siteone-crawler) | Evaluate and integrate **first** | CLI-based, read-only technical crawl evidence → normalized data model |
| [CrawlSEO](https://github.com/crawlseo/crawlseo) | Isolated evaluation; **not adopted yet** | Determine whether its GSC/Core Web Vitals capabilities justify another service |
| Official [Google Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index) | Read-only alternative to CrawlSEO integration | Search Analytics, sitemap records and indexed-version URL Inspection; not a live URL test |
| Sustainable Catalyst | Custom product | Quality review, editorial provenance, KB/Research/Publication integration, evidence-backed priority queue |
| Keyword, backlink and rank suites | Deferred | Only after value, licensing, privacy and budget evaluation |

## Build stages

| Milestone | Deliverable | Exit condition |
| --- | --- | --- |
| **SC-SEO-02.0** | Offline Search Console XLSX + optional HTTP TSV imports; static HTML/CSV/JSON reports | Synthetic CI, private data audit and real-export replay reviewed. In draft PR #1, not merged/deployed. |
| **SC-SEO-02.1A** | Pin SiteOne version, capture genuine synthetic JSON and check CLI/report license/compatibility | Exact upstream release, checksum, data fields and run command documented |
| **SC-SEO-02.1B** | `seo.url-observation.v1` typed records + SiteOne JSON importer and sample fixtures | Deterministic joins, provenance, redirects, robots/canonicals, malformed input and escaping tests |
| **SC-SEO-02.1C** | Evaluate CrawlSEO vs a small direct GSC read-only adapter | Written accept/reject with security, maintenance, scopes and resource costs; no production OAuth |
| **SC-SEO-02.1D** | End-to-end isolated staging analysis | Baseline counts, source hashes, error cases and import receipts reproducible; no production writes |
| **SC-SEO-02.2** | Authenticated Workspace/Publication Studio, WordPress metadata read adapter and approved GSC read connectivity | Cross-owner denial, credential storage, retention, backup/rollback and Platform Core approval |
| **SC-SEO-02.3** | Publication quality and Research/KB provenance pilot | Human-reviewed matched sample of 20 indexed + 20 unindexed pages; transparent methodology |
| **SC-SEO-02.4** | Recovery and ongoing monitoring | Owner-approved fixes only; validation status tracked without indexing guarantees |
| **SC-SEO-02.5** | Optional keyword/competitor intelligence | Separate cost and need decision |

## Data flow

```text
GSC exports / optional official API  +  SiteOne JSON  +  WordPress read-only
                                 |
                       Source-specific adapters
                                 |
                   seo.url-observation.v1 (typed)
           original URL, source, timestamp, SHA-256, status
                                 |
                SEO Intelligence core + review queue
                                 |
          local report (02.0) / authenticated UI (02.2+)
```

**Never collapse Google's past indexed-version classification into a current SiteOne/ordinary HTTP result.** An ordinary HTTP 200 does not demonstrate verified Googlebot access or Google indexing. Missing facts stay explicitly unknown. Similar slugs and 'crawled—not indexed' do not prove duplicate/low-quality content.

**Implementation tracker:** [SC-SEO-02.1 issue #2](https://github.com/Content-Catalyst-LLC/sustainable-catalyst-seo-intelligence/issues/2).

## Immediate engineering sequence

1. Review and accept v0.1.0 source PR and private evidence boundaries.
2. Pin a SiteOne release and record one **real upstream JSON schema example using synthetic pages**.
3. Implement and test a read-only adapter and versioned URL-observation contract with no network in CI.
4. Evaluate CrawlSEO in disposable isolation and choose either a limited GSC adapter or an approved upstream integration.
5. Submit evidence receipts and updated documentation; only then plan authenticated Workspace integration.

## Security and release rules

- Code, test fixtures and public docs belong here; *real Search Console exports, full crawl results, owner-specific dashboards and sensitive operational data do not*.
- Private, approved evidence may live in `Content-Catalyst-LLC/data/evidence/seo/`; **never put OAuth tokens/API keys in either Git repository**.
- No default crawl of authenticated routes, no unrestricted network scans, no Googlebot impersonation or unapproved external text processing.
- No automatic indexing requests, article changes, redirects, sitemap changes, WordPress modifications or production deployments.
- Require version pinning, provenance hashes, synthetic/negative tests, approval, rollback and environment-specific acceptance before release.

Private cross-repository architecture and handoff are maintained in Content-Catalyst-LLC/documentation and Content-Catalyst-LLC/handoffs.
