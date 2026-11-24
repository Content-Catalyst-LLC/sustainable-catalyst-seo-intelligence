# Importing a prior sitewide audit as a versioned baseline

`seo_baseline.py` maps the SC-SEO-01 sitewide audit (2026-11-23) onto `seo.evidence-history.v1`. It adds no crawler and
no analyser: observations come from the audit's own capture, and checks run through the existing evidence rules plus two
ported validators.

| Source artifact (private) | Evidence | Reused component |
| --- | --- | --- |
| Crawl JSONL (one row per request) | `http_crawl` observations: status, redirect hops, canonicals, robots, title/description, H1s, JSON-LD validation, TTFB, cache headers | `EvidenceHistory.add`, `build` |
| Same crawl, internal links | `broken_link` relations (links from captured HTML pages to URLs the crawl saw fail) | existing `broken_link` finding |
| URL-level issue register | `audit_finding` records, verbatim, tagged with the analyser's SHA-256 | shown separately from recomputed findings |
| 32 grouped issues | run-level `baseline.issue_groups` | — |
| Sitemap index + leaf XML | `sitemap` membership; completeness | `EvidenceHistory.sitemap`, `reconcile_sitemaps.sitemap_membership` |
| WordPress status export; KB governance export | `wordpress` records with states copied verbatim | `EvidenceHistory.publications` |
| Redirect proposals + current rules | `baseline.redirect_verification` | `seo_redirect_proposals` |
| Release batches + dated production observation | `change_events` | — |

**Provenance rules**
- Every input is SHA-256 pinned in the receipt, and every observation names its source digest and record ordinal.
- The crawl rows have no request times, so each observation's source time is `null`. The run window is recorded once in `baseline.observed_window`, and is never copied onto rows.
- The collection policy recorded is the one actually used.
- A row that fails the property URL policy is itemised in `rejected_rows`, never silently dropped.
- Verification states are never upgraded. A KB record without a WordPress status is skipped and counted.

**Reconciliation**

The receipt compares these imported counts with the original audit summary:
- requests, HTML pages, status distribution, sitemap URLs;
- broken-link targets and edges;
- URL-level findings, overall and by priority;
- issue groups.

The command exits 2 if any comparison differs.

```bash
python3 seo_baseline.py --evidence-dir /private/sc-seo-01/evidence --release-dir /private/sc-seo-01/release \
  --tool-dir /private/sc-seo-01/tools --issue-groups /private/sc-seo-01/deliverables/SC-SEO-01-ISSUES.csv \
  --output-dir /private/baseline-import --imported-at 2026-11-24T14:00:00Z
```

## Ported validators

| Original (SC-SEO-01) | Port | Equivalence evidence (private inputs, aggregate result) |
| --- | --- | --- |
| `ldcheck.py` (fetched pages itself) | `seo_structured_data.py` (validates captured blocks; no requests) | Original and port on the same 58 inputs (55 captured pages plus 3 edge cases), 7 structured-data fields each: **0 differences** |
| `check_redirects.py` (fetched each URL itself) | `seo_redirect_proposals.py` (evaluates observations from the existing collectors) | Original run with its HTTP served from the same observations: 109 approved + 13 held + 6 synthetic failing proposals, **0 verdict differences** |

**Differences, intended:**
- An unobserved URL is reported as *unknown*, where the original reported "currently None".
- The original never detected an existing-rule collision. It kept the trailing newline when reading the rule list, so a rule `"/a/\n"` became `"/a/\n/"`. The port strips it. Earlier collision conclusions were established independently and remain valid.

The two SC-SEO-01 tools are not retired here. Retirement follows owner review of this equivalence evidence.
