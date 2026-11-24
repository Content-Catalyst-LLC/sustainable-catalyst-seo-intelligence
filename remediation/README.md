# Remediation (the only write path)

SEO Intelligence analyses evidence and proposes changes; it never writes to a website. Code under `remediation/` is the
separate, controlled mechanism that applies an **owner-approved, locked** proposal and can roll it back.

| Path | Target | Contract |
| --- | --- | --- |
| `wordpress/sc-seo-01/` | WordPress (WP-CLI) | `plan` → `lock` (hash of every targeted value) → `apply <lock>` (refuses on drift, writes a backup first) → `rollback <backup>` (refuses values edited after apply). No rewrite flush. |

Rules:

- Nothing here runs from CI or on a schedule. An operator runs it after a separate, per-batch authorization.
- Inputs are proposal files produced and reviewed outside this repository; site-specific changesets, locks, backups
  and host details stay in private storage.
- After apply, verification is an ordinary read-only observation that goes back into the evidence history.
