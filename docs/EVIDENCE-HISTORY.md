# SEO evidence history v1

The existing Studio CLI writes `seo-dashboard.html`, summary JSON and the legacy inventory and queue CSVs to the supplied local output directory. The summary and embedded dashboard payload include `evidence_history`, described by [the contract](../contracts/seo.evidence-history.v1.schema.json). The standalone SiteOne importer retains `seo.url-observation.v1`.

```bash
python3 seo_studio.py --input-dir /private/approved-exports \
  --property https://example.test/ --siteone-json /private/crawl.json \
  --sitemap-xml /private/sitemap.xml \
  --html-response https://example.test/guide /private/guide.html \
  --publications-json /private/publications.json \
  --output-dir /private/studio-report
```

You may provide an empty `--input-dir` for crawl-only analysis. Repeat `--siteone-json`, `--sitemap-xml` and `--html-response URL FILE` for multiple inputs. Use `--imported-at` with a fixed timezone-aware timestamp for deterministic replay.

| Source | Inputs and evidence |
| --- | --- |
| Google Search Console | Supported XLSX exports in `--input-dir`. Page observations preserve the recognized export class and raw last-crawled value. The coverage summaries and trends use the existing latest-by-filename selection for each export category. Treat the input directory as one property's exports. |
| SiteOne | Local JSON. Status, redirect hop, SEO fields and broken-link source relationships. Results use the v1 importer identity, and relationship records identify the relevant upstream table and ordinal through their kind and facts. |
| Historical HTTP | Optional `--http-tsv`, with `url`, `http_status`, optional `final_url`, `redirect_count` for legacy inventory and `observed_at` for source history. |
| Sitemap | One or more local UTF-8 XML leaf `urlset` files. Positive membership is an observed fact. Absence from the provided subset stays unknown. Resolve sitemap indexes separately. |
| Publication | Explicitly authorized local JSON containing an array of `publication_id`, `url`, `status` and optional `modified_at` strings. Only the listed fields are retained. |
| HTML response | Explicitly paired local HTML body and URL. Canonical links and generic robots meta in the explicit HTML head are parsed as text. The body digest and declared URL identify the capture. Header-only directives, a capture time absent from the input, browser rendering and HTML base URL semantics are outside this importer. |

Each artifact records the SHA-256 of bytes read, basename, source kind and import time. Rows also retain input URL, comparison key, stable observation ID and source time or null. Ordinals are zero-based where provided. Exact repeated SiteOne input is deduplicated within a run. Different rows in the same report remain distinct. The current comparison policy matches the exact configured host, maps standard same-host HTTP to the HTTPS property, and preserves paths, query strings and percent encoding. Dedicated loopback imports require the exact fixture port. This is a URL correlation policy with original URLs retained for review.

Findings are review suggestions tied to observation IDs. A successful crawl never changes a Google classification. Sitemap mismatches indicate that the supplied artifacts warrant investigation, since source collection times can differ. Broken-link findings link the referring page to the failed target. Use the dashboard’s technical section to filter investigations by URL, publication ID or presence of findings. Workspace owner authorization, durable cross-run storage and connector credentials are deferred to SC-SEO-02.2. Keep operational reports and source inputs in restricted storage.
