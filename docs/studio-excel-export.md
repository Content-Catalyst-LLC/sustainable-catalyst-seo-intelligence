# SEO Studio — Excel review export (SC-SEO-STUDIO-EXPORT-01)

Phase 1 (implemented): a read-only `.xlsx` review workbook generated from the stored evidence run.
Phase 2 (design only, not implemented): a controlled re-import of reviewer decisions.

## Phase 1 — export

### Where it lives
| Part | File |
|---|---|
| Workbook assembly from the evidence store | `seo_studio_export.py` |
| Dependency-free `.xlsx` writer (stdlib `zipfile` + XML) | `seo_xlsx.py` |
| Authenticated route `GET /api/export.xlsx` | `seo_studio_server.py` |
| "Export to Excel" control | `studio/index.html`, `studio/app.js`, `studio/app.css` |
| Tests (synthetic only) | `tests/test_studio_export.py` |

No new runtime dependency; the Studio image is unchanged apart from the new files.

### Request
`GET /api/export.xlsx?run=<run_id>&scope=run|filtered[&filter=<pages filter>][&q=<url contains>][&kind=<recommendation kind>]`

* Same `SC-Workspace` session check as every other `/api/` route (allowlisted subject, SEO audience, signature,
  expiry). Unauthenticated or refused → `401`; unknown run, scope or filter → `400`.
* `scope=run` exports the entire run and ignores filter parameters. `scope=filtered` applies the Pages filter and
  URL text to every URL-keyed sheet (and to recommendations whose subject is in that URL set) plus the
  Recommendations kind. These are the same filter definitions the Pages API uses, so row counts equal the API totals.
* One export is built at a time per Studio process (`429` with `Retry-After` otherwise).
* Response: `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
  `Content-Disposition: attachment`, `Cache-Control: no-store`.

### Workbook
| Sheet | One row per | Present |
|---|---|---|
| Overview | export metadata, run provenance, row counts, omitted sheets, legend, instructions | always |
| Recommendations | stored recommendation (store ID) | always |
| URL Inventory | page in scope | always |
| Indexing Issues | crawl indexability finding (HTTP failure, redirect, canonical, noindex, sitemap) and Search Console observation | if rows |
| Broken Links | broken-link finding (source page → target) with related redirect / link-repair recommendation IDs | if rows |
| Metadata & H1 | title / description / H1 finding with the page's crawled title, description and H1 | if rows |
| Category Archives | `/category/` URL in scope | if rows |
| Other Findings | finding not in the categories above (e.g. duplicate content, orphan, TTFB), so no evidence is dropped | if rows |
| Evidence & Sources | every artifact of the run and every observation referenced by another sheet, with SHA-256 | always |

Omitted sheets are listed on the Overview with the reason.

Every data sheet has:
* **Row 1:** a banner stating that the run is a historical snapshot and not current site state. It gives the
  run label, ID, observed window and import time, the export time and the scope.
* **Row 2:** a header that is frozen, with filters enabled. Columns have widths set, long text wraps, and URLs are
  clickable `http(s)` links.
* **Evidence columns:**
  * *Record ID*, *Run ID* and *Evidence class* (measured / prior / inferred / search-console);
  * *Evidence type*, e.g. "HTTP crawl (run snapshot)" or "Search Console API (historical observation)";
  * *Observed*: the run window for crawl and sitemap evidence, and the pull time and reporting range for Search
    Console evidence;
  * evidence references (observation IDs);
  * *Row fingerprint*.
* **Review columns** (highlighted, the only cells meant to be edited):
  * *Review decision*: Approve / Reject / Defer / Needs information (dropdown);
  * *Review status*: Not started / In review / Done (dropdown);
  * *Reviewer notes*, *Reviewed by*, *Reviewed on (YYYY-MM-DD)*.

  Sheets are not protected, so sorting and filtering keep working.

### Record IDs (stable for a run)
| ID | Source |
|---|---|
| Recommendation | `recommendation.recommendation_id` from the store (`sha256(kind:subject)[:20]`) |
| `F-` finding | `sha256(run_id, url, evidence_class, priority, action, observation_id)[:16]`, with `-n` added for exact duplicates |
| `U-` URL | `sha256(run_id, url)[:16]` |
| `S-` Search Console | `sha256(run_id, observation_id)[:16]` |
| Row fingerprint | `sha256` over the row's evidence cells as text, joined by U+001F (first 16 hex chars) |

The row fingerprint is recomputable from the cells a spreadsheet returns.

### Evidence currency
The export never describes a stored run as current. The banner, the Overview's *Evidence currency* row and each
row's *Observed* column all carry the run's observation window (for the SC-SEO-01 baseline: operator-reported
start 2026-11-23T20:00Z, end 2026-11-23T22:59Z). Search Console values are labelled *historical* with their
own pull time and reporting range. When a run has no Search Console evidence, the Overview says so and indexing
status is not inferred from crawl signals.

### Safety
* **Read-only.** The workbook is built from a separate `mode=ro` SQLite connection with in-memory temp storage.
  Nothing is written to the store, to WordPress or anywhere else. The store hash is unchanged by an export (tested).
* **Access control.** The route uses the same session check as the dashboard. Only the authorised run data is
  exported: the store holds only evidence the Studio already shows.
* **No secrets.** The workbook contains no session, signature, key or credential. Only the signed-in Workspace
  subject is recorded, for the audit trail (tested).
* **Formula injection.** No formula is written (no `<f>` element). Text a spreadsheet could read as a formula
  (leading `= + - @`, tab, CR, or their full-width forms) gets Excel's *quote prefix*, so it stays literal text,
  even after editing.
* **Links.** Only `http`/`https` URLs become links, as external relationships, never `HYPERLINK()`.
* **Invalid input.** XML-illegal control characters are removed. Cells over 32,767 characters are cut and marked.

### Measured on the staging evidence (read-only, under the Studio container's 256 MiB limit)
Whole-run export of the SC-SEO-01 baseline:
* **Size and speed:** 4.9 MB, 5.2 s, peak RSS 106 MB.
* **Rows:** 1,001 recommendations; 3,751 URLs; 12,060 findings (1,786 indexing, 3,139 broken-link, 6,995
  metadata/H1, 140 other); 477 category archives; 13,869 evidence rows.
* **Readers:** read back fully by Microsoft Excel (sheet ranges, links and filters checked), LibreOffice and openpyxl.

## Phase 2 — controlled re-import of reviewer decisions (design only)

**Goal:** record reviewers' decisions, statuses and notes from a returned workbook in the review record. Evidence
and recommendations stay untouched. **Never** change WordPress or any website. Approved items still ship only as
separately authorised remediation batches.

### Storage
A new append-only table in a **separate review database**, not the evidence store, which stays read-only for the
Studio:

```
review_decision(run_id, record_type, record_id, decision, review_status, notes, reviewed_by, reviewed_on,
                imported_at_utc, imported_by_subject, workbook_sha256, row_fingerprint, supersedes)
```

* It is append-only, like the evidence store: a later import adds a new row that `supersedes` the earlier one.
  The current decision is the latest row per `(run_id, record_type, record_id)`.
* `imported_by_subject` is the Workspace subject of the session that confirmed the import.

### Flow
1. **Upload** (`POST /api/review-import/preview`, same session check, `Content-Type` must be the xlsx type, size
   ≤ 25 MB).
   * The server parses the package with the stdlib reader, with no macros or external links followed. It rejects
     `.xlsm`, encrypted files and files with no Overview.
   * Nothing is stored except a short-lived preview held in memory (or a `0600` temp file), keyed by a random
     preview ID and bound to the uploader's subject.
2. **Validate** each data row:
   * the Overview's *Export schema* equals `sc-seo-studio-export/1` and its *Run ID* exists. The workbook's run
     must match the row's *Run ID*;
   * `record_id` exists in that run (recommendation IDs from the store; `F-`/`U-`/`S-` IDs recomputed from the
     store exactly as the export derives them);
   * the row fingerprint recomputed from the returned evidence cells equals the *Row fingerprint* cell. A mismatch
     means evidence cells were edited, so the row is **rejected** (reviewer notes belong in *Reviewer notes*);
   * the decision and status values are from the listed choices; *Reviewed on* is a valid date no later than
     today; notes are ≤ 4,000 characters after removing control characters;
   * duplicate record IDs in one workbook are rejected. Rows with all review cells blank are ignored.
   * Formula cells are read as their cached text only and never evaluated. A cell holding a formula is rejected.
3. **Preview** (response to the upload):
   * counts of new decisions, changed decisions (old → new) and unchanged rows;
   * rejected rows with sheet, row number, record ID and reason;
   * any rows where the workbook's run is not the latest run (shown, not blocked).
   * Nothing is written yet.
4. **Confirm** (`POST /api/review-import/confirm` with the preview ID and an explicit `confirm: true`).
   * It must be the same subject, within 30 minutes, and the preview must be unchanged. The server re-checks that
     the review database still matches the preview's base state.
   * It then inserts the accepted rows in one transaction. The import record (file SHA-256, counts, subject,
     time) is written in the same transaction.
5. **Audit and undo:** Studio shows decision history per record. A mistaken import is undone by importing a
   corrected workbook, which appends superseding rows. Nothing is deleted.

### Explicitly out of scope
* Any write to WordPress, redirects, metadata, Search Console or the evidence store.
* Automatic creation of remediation batches from *Approve* decisions. A human prepares batches from the decision
  list through the existing authorised release process.
* Evaluating formulas, macros or external links in uploaded files.

### Enabling Phase 2 later would need
* A writable review volume separate from the read-only evidence volume.
* A Studio change from GET-only to the two authenticated POST routes, with CSRF defence (the `Authorization`
  header is required, not a cookie, so no ambient credential is sent; `Origin` is checked as well).
* The CSP `form-action` stays `'none'`, since uploads go through `fetch`.
* Tests mirroring the Phase 1 ones: fingerprint tamper, wrong run, unknown ID, duplicate, invalid values,
  formula cells, oversized file, concurrent confirm, and proof that the evidence store is unchanged.
