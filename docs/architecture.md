# SC-SEO-02 architecture and release boundaries

**Current implementation:** offline proof of workflow (v0.1.0)
**Production authorization:** NO-GO

## Data flow

Local Search Console XLSX exports + optional SC-SEO-01-403 ordinary HTTP TSV (legacy 403 check)
→ seo_studio.py (read-only parsing, URL normalization, classification and evidence reconciliation)
→ local HTML, CSV, JSON reports (not for public serving).

Historical Search Console classification, historical crawl date and ordinary HTTP response are separate evidence classes. A 200 response is **not** proof of verified Googlebot access or Google indexing.

## Stage map

| Capability | v0.1 state | Later work |
| --- | --- | --- |
| Search Console exported URL examples | Implemented | Schema validation, source hashes, import receipts |
| Indexed vs. unindexed comparison | Basic counts/type classifications | Matched-pair editorial review |
| Historical 403 + HTTP TSV reconciliation | Implemented | Bot validation, access log evidence |
| Issue queue and dashboard | Offline local HTML | Authenticated Workspace/Publication Studio |
| Publication quality | Manual review prompts only | Traceable scientific evidence review |
| API connection | Not implemented | Permissioned Search Console and WordPress read-only APIs |
| Scheduled monitors | Not implemented | Approved recurring snapshot cadence |
| Production remediations | Not implemented | Separate owner-approved PRs and rollback |
| Data retention | Local operator controls | Private evidence store policy and deletion tests |

## Security boundaries

1. Public source can never include actual GSC exports, auth material, private datasets, or generated report HTML.
2. Restricted, approved evidence may be stored under Content-Catalyst-LLC/data/evidence/seo/; every artifact needs an owner, timestamp, hash, rights basis, sensitivity, and retention plan.
3. OAuth tokens and secrets go to dedicated secret management, never Git.
4. The SEO application may recommend but must not silently issue redirects, noindex changes, Google validation, sitemap changes, or bulk publication.
5. Production identity, data transport, audit logging, backup/restore and security acceptance require explicit approval and isolated verification.

## Acceptance criteria — current release

- A synthetic XLSX/TSV import generates all four local report artifacts.
- The importer distinguishes Google historical state from current HTTP.
- Audit classification never converts "unindexed" into a fabricated "low quality" label.
- Generated HTML uses text-only DOM insertion and escapes script-bearing JSON.
- Only synthetic tests and docs are committed publicly.
- No external network, crawler or Google API requests by default.

## Constraints

Google Search Console URL Inspection API reports Google's indexed-version information, **not live testing**. Google Page Indexing UI exports may provide only a subset of affected URLs; counts vs examples must remain separately labeled. Google supports multiple sitemaps, so overlap alone is not a problem.

References:
- https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect
- https://developers.google.com/webmaster-tools/v1/api_reference_index
- https://support.google.com/webmasters/answer/7440203
