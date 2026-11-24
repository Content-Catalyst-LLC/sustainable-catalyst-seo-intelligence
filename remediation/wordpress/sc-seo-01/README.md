# SC-SEO-01 — WordPress search hygiene

This directory holds the WordPress-side implementation for the SC-SEO-01 sitewide SEO assignment. It is separate from the offline SEO Intelligence Studio, which never writes to WordPress. Nothing here runs automatically, and production use requires explicit owner approval.

| File | What it is | Reversible by |
|---|---|---|
| `sc-seo-01.php` | Must-use plugin. (1) Types Knowledge Base guides (`sc_support_article`) as `TechArticle` in Rank Math JSON-LD. (1b) Removes the Rank Math `Article` node from WordPress **pages**, which are hubs and application pages, while keeping WebPage, WebSite, Organization and BreadcrumbList. (1c) **KB sitemap quality gate:** a guide is left out of the sitemap while its integrity state is `blocked` or its own published *Status:* line says pending, draft or not production-verified, and the redirecting KB archive is never listed. The gate changes no robots directives. (2) An **opt-in** switch (`sc_seo_01_external_custom_css`, off by default) that serves Customizer *Additional CSS* as a versioned, cacheable stylesheet at the same `<head>` position instead of inlining it into every page. | Delete the file. For (2), set the option to `0`, or delete the option and `uploads/sc-seo-01/`. |
| `sc-seo-01-changeset.php` | WP-CLI tool for Rank Math settings and per-post SEO meta. Modes: `plan` (no writes), `apply` (writes a full backup of every prior value first), and `rollback <backup.json>`. It never changes content, status, slugs or dates. It refuses unpublished posts and post-ID mismatches (`expect_id`). | `rollback` with the backup written by `apply`. |
| `sc-seo-01-kb-h1.php` | WP-CLI tool that removes a KB article *body* `<h1>` only when it repeats the post title, because the template already renders the title as the page H1. Plan, apply and rollback modes; full prior-content backup; saves through `wp_update_post`, which records a revision; skips any `<h1>` that differs from the title. | `rollback <backup.json>`, which refuses posts edited after apply. |
| `sc-seo-01-dedupe-repeated-page.php` | WP-CLI tool for a page whose stored content was saved several times over (the whole page repeated three times). It acts only on a published page whose content still matches an audited MD5, and only when the copies are verified verbatim repeats; it keeps exactly one copy. Plan, apply and rollback modes; full prior-content backup; saves through `wp_update_post` (revision recorded). | `rollback <backup.json>`, which refuses pages edited after apply. |
| `changes.options.example.json` | The reviewed Rank Math settings changeset: KB and foundation-document sitemaps, Organization knowledge graph, consistent site name, KB title and schema defaults, homepage title de-duplication. | Backup and rollback. |

Per-page descriptions and robots changes are site operational data. They live in the private SC-SEO-01 evidence store, not in this public repository.

## Why setting changes rather than code

Rank Math's sitemap can only *exclude* post types through filters. Including a post type requires the setting `sitemap.pt_<type>_sitemap = on`. Setting `pt_page_default_rich_snippet` to `off` was tested and **rejected**: it removes the entire WebPage, WebSite and Organization graph from pages. The mu-plugin's page filter removes only the Article node instead.

## Procedure (production, after approval)

```bash
# 0. backup (DB dump with utf8mb4, per the host runbook) and record current rendered HTML
wp eval-file sc-seo-01-changeset.php plan  changes.options.json            # review
wp eval-file sc-seo-01-changeset.php lock  changes.options.json > lock.options.json   # at review time
wp eval-file sc-seo-01-changeset.php apply changes.options.json  ~/sc-releases/backups/<ts>-sc-seo-01-options.json lock.options.json
wp eval-file sc-seo-01-changeset.php apply changes.page-meta.json ~/sc-releases/backups/<ts>-sc-seo-01-page-meta.json
cp sc-seo-01.php wp-content/mu-plugins/                                     # schema typing + KB sitemap gate (before enabling KB sitemaps)
wp eval-file sc-seo-01-kb-h1.php plan                                       # review
wp eval-file sc-seo-01-kb-h1.php lock > lock.kb-h1.json                       # at review time
wp eval-file sc-seo-01-kb-h1.php apply ~/sc-releases/backups/<ts>-sc-seo-01-kb-h1.json lock.kb-h1.json
wp eval-file sc-seo-01-dedupe-repeated-page.php plan  <id>:<audited-md5> ...  # only after owner confirms the page text
wp eval-file sc-seo-01-dedupe-repeated-page.php apply ~/sc-releases/backups/<ts>-sc-seo-01-dedupe.json <id>:<audited-md5> ...
# verify anonymously (sitemap index, KB sitemap, JSON-LD types, titles), then purge the page cache

# rollback
wp eval-file sc-seo-01-changeset.php rollback ~/sc-releases/backups/<ts>-sc-seo-01-page-meta.json
wp eval-file sc-seo-01-changeset.php rollback ~/sc-releases/backups/<ts>-sc-seo-01-options.json
rm wp-content/mu-plugins/sc-seo-01.php
wp eval-file sc-seo-01-kb-h1.php rollback ~/sc-releases/backups/<ts>-sc-seo-01-kb-h1.json
wp eval-file sc-seo-01-dedupe-repeated-page.php rollback ~/sc-releases/backups/<ts>-sc-seo-01-dedupe.json
```

**Locks.** `apply` requires the lock captured when the change was reviewed and refuses if any targeted value or post has changed since, so a reviewed changeset can never overwrite a newer edit. Changeset rollback likewise refuses keys edited after apply unless `--force` is given. The tools never flush rewrite rules (a WP-CLI flush drops Rank Math's sitemap rules); they invalidate the Rank Math sitemap cache, the object cache and the page cache.

The content tools call `kses_remove_filters()` for their own writes: WP-CLI runs without a user, and the content they save is derived from content an administrator already stored, so it must not be re-filtered.

The custom-CSS switch is a separate approval with its own visual regression check, because it changes how about 1.1 MB of CSS reaches the browser. It does not change what that CSS is.

```bash
wp option update sc_seo_01_external_custom_css 1   # enable
wp option update sc_seo_01_external_custom_css 0   # disable; core inline output resumes immediately
```

## Development verification

Verified against a disposable WordPress 6.8, PHP 8.3 and MySQL 8.0 stack with Astra 4.14.3 and Rank Math 1.0.279, the same Rank Math version as production. The stack was seeded with production's Rank Math *titles* and *sitemap* option values (no secrets) and with stand-in post types registered using production's arguments. Results are recorded in the private `SC-SEO-01-IMPLEMENTATION.md`.
