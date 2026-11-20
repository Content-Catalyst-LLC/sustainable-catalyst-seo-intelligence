# SC-SEO-02.1 — SiteOne JSON adapter (first implementation)

**Release state:** The macOS ARM64 v2.5.1 binary passed a disposable loopback crawl and deterministic replay. See [the acceptance report](SITEONE-ACCEPTANCE.md).

## Upstream version and format evidence

- Pinned **evaluation release:** SiteOne Crawler [v2.5.1](https://github.com/janreges/siteone-crawler/releases/tag/v2.5.1), released June 27, 2026, MIT license (copyright Ján Regeš).
- Published JSON shape inspected at tag `v2.5.1`: [JSON-OUTPUT.md](https://github.com/janreges/siteone-crawler/blob/v2.5.1/docs/JSON-OUTPUT.md) and [OUTPUT-crawler.siteone.io.json](https://github.com/janreges/siteone-crawler/blob/v2.5.1/docs/OUTPUT-crawler.siteone.io.json).
- **Important:** The bundled upstream sample embedded in the `v2.5.1` tag self-identifies as **crawler 2.0.0.20260316**. The actual v2.5.1 loopback shape is now covered by a separate captured projection and executable acceptance runner. Local synthetic fixture `fixtures/siteone-synthetic-v2.json` intentionally uses documented keys. It is **NOT** captured crawler evidence.
- Release assets' **GitHub-published digests**, not locally rehashed downloads:
  - macOS ARM64: `siteone-crawler-v2.5.1-macos-arm64.tar.gz` SHA-256 `6b893a15ce695a002bbe6c3d2467954fee64059216d471fae55c781936d96e2f`
  - macOS x64: `siteone-crawler-v2.5.1-macos-x64.tar.gz` SHA-256 `724639bdfca7d26514a7c3f10caa6a9172a4f2192744d9e4691bcf842b2e4609`
  - Linux x64: `siteone-crawler-v2.5.1-linux-x64.tar.gz` SHA-256 `09278d958d4a087fa46093805cd33b085b96618001dd31d45c448ad724c9024e`

## Input contract

The adapter reads local JSON files with SiteOne `crawler` and `results` objects. It accepts documented `results[]` fields (`url`, `status`, `type`) and optional `tables.seo.rows[]` (`urlPathAndQuery`, `title`, `description`, `h1`, `robotsIndex`, `indexing`, `deniedByRobotsTxt`) and `tables.redirects.rows[]` (`url`, `targetUrl`). A redirect **target** may be a single hop, not the final resolved destination. No `canonical` or sitemap inclusion is inferred if absent from observed fields. No quality score or Google ranking is fabricated.

The upstream crawler's `executedAt` field is specified as a local timestamp **without an offset**; it is preserved as raw, never silently interpreted as UTC. The separate importer time is UTC and cannot replace the crawl time.

A strict HTTPS property hostname allowlist prevents out-of-scope URLs from entering the joined inventory. URL comparison keys preserve paths and query strings and avoid collapsing distinct resources. The raw URL remains attached to every observation. The adapter never makes HTTP requests or executes the SiteOne binary.

## Run the synthetic demo (no network)

```bash
python3 seo_siteone.py \
  --siteone-json fixtures/siteone-synthetic-v2.json \
  --property https://example.test/ \
  --imported-at 2026-11-20T12:31:00Z \
  --output-dir /tmp/sc-seo-02-siteone-synthetic

python3 -m unittest discover -s tests -v
```

After approved SiteOne collection, point `--siteone-json` at an actual private report file, `--property` at the exact allowed origin, and `--output-dir` to a **private directory outside the public Git checkout**. Optionally add `--studio-inventory` pointing at a previously generated `seo-url-inventory.csv`; the reconciled CSV retains Google historical states in their own columns.

**Outputs:** `seo-siteone-observations.json`, `seo-siteone-import-receipt.json` (input SHA-256, version, count of accepted/rejected rows, output digest, source-time caveat), and optional `seo-siteone-reconciled-inventory.csv`. These may contain private site URLs, metadata and operational evidence and must never be committed to public Git.

## Remaining acceptance gates

- [x] Run the checksum-pinned SiteOne binary against the disposable loopback fixture and record the result.
- [x] Confirm loopback fixture scope, robots behavior, request settings and local output.
- [ ] Replay parsed local report with prior Search Console exports; verify representative redirect, 404, 403, canonical/noindex cases.
- [ ] Document security/privacy acceptance and standalone integration into authenticated Studio; review output headers/CSV formula injection prevention.
- [x] Document the [CrawlSEO versus direct GSC API decision](CRAWLSEO-DECISION.md).