# SC-SEO-02.1D · SiteOne acceptance

**Result:** SiteOne v2.5.1 macOS ARM64 binary and offline Studio replay passed against a disposable, unauthenticated loopback fixture on November 20, 2026. The public-site crawl awaits an approved origin and path allowlist. This receipt covers loopback acceptance.

```bash
bash scripts/accept_siteone.sh /tmp/seo-siteone-acceptance-new-directory
python3 -m unittest discover -s tests -v
```

The runner checks the archive digest before extraction and compares the packaged MIT LICENSE to the file at the tagged release. It starts only `127.0.0.1:18765`, uses one worker, a one-request-per-second crawl limit, depth 1, a 12-URL cap and a dedicated disposable cache. It uses the identified Sustainable Catalyst test user agent. Browser, AI, upload and mail features remain off. The runner asserts that the disallowed path received no GET. It then captures the local XML and a separate response body and header for reliance on explicit technical facts.

| Check | Actual result |
| --- | --- |
| Release | [SiteOne v2.5.1](https://github.com/janreges/siteone-crawler/releases/tag/v2.5.1) self-reported build `2.5.1.20260627` |
| macOS ARM64 archive SHA-256 | `6b893a15ce695a002bbe6c3d2467954fee64059216d471fae55c781936d96e2f` |
| Packaged MIT LICENSE SHA-256 | `05c547dbb0d82224a3306a74aae9108453bfa55fce736e1e310df3c8c525092b` |
| Successful raw JSON SHA-256 | `765ab55239d781513ae4f6954f94551832d71f82975c773539a46e488fdc0a95` |
| In-scope crawler results | 7 accepted, 0 rejected. Four 200 responses and one each of 301, 403 and 404 |
| Redirect and broken link | Relative `/guide` resolves as the target of `/old`. The 404 table connects `/` to `/missing`. |
| Crawl and indexing directives | No request to `/denied`. SiteOne reported `robotsIndex: "0"` for `/noindex`. Separate capture confirmed the response's `X-Robots-Tag` header, and Studio imported the HTML robots meta. |
| Canonical, headings and sitemap | SiteOne recorded the guide H1 and an empty H1 on `/noindex`. The JSON included an aggregate canonical diagnostic and no per-page target. Studio parsed the independently captured HTML canonical for `/noindex` and flagged its supplied XML membership with those technical observations. |
| Reproducibility | Two imports from the same bytes with the same explicit import time emitted byte-identical JSON, HTML and CSV files. A new crawl has new timing data and can have different order and digest. |
| Google classification | No Google classification was emitted from the loopback HTTP results. Synthetic mixed-source tests separately verify preservation of historical Search Console observations. |

`fixtures/siteone-2.5.1-loopback-projection.json` retains the consumed JSON shape and selected tables from an initial successful intra-loopback run. The projection omits timings, local paths, runtime environment diagnostics and other unconsumed sections. The original synthetic fixture and tests are retained. The runner writes raw JSON, server log, auxiliary response captures, both imports and a checksum-only receipt into its private output directory. Hosted CI runs the offline tests only.

Live Google, WordPress and public-site behavior remain outside this result. The first-party Search Console connector is the recommendation for the next milestone. This change implements no live authentication or production integrations.
