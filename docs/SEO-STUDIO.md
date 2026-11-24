# SEO Studio: persistent evidence and authenticated dashboard

| Component | Role | Writes? |
| --- | --- | --- |
| `seo_store.py` | Append-only evidence store. SQLite in development; PostgreSQL DDL in `contracts/sql/seo-store.postgres.sql` | Appends runs only. UPDATE and DELETE are refused by trigger |
| `seo_recommendations.py` | Inferred opportunities, each citing its observations | No. Approval is required and nothing is applied |
| `seo_workspace_auth.py` | Verifies Workspace-signed `sc-workspace-seo-session/1.0` documents | No |
| `seo_studio_server.py` + `studio/` | Read-only dashboard and paginated JSON API | No. Opens the store read-only and makes no outbound requests |
| `seo_gsc.py` | Read-only Search Console connector, prepared and inactive | No. Manual, owner-authorized runs only |
| `scripts/dev_workspace_session.py` | Development-only session issuer with an in-memory key, loopback only | No |

## Evidence classes
The dashboard keeps three classes of evidence apart:
- **Measured**: recomputed by the evidence rules from observations in the run.
- **Prior**: a previous audit's classification, kept verbatim with the analyser's hash.
- **Inferred**: a recommendation awaiting human approval.

The dashboard shows indexing only from search-engine evidence. It does not infer indexing from HTTP status or sitemap membership.

## Identity
SEO Studio reuses the Workspace identity used by Research:
- The signature is the same Ed25519 construction, with the same issuer, subject format, 300-second lifetime and 60-second clock skew.
- The schema and audience are different (`sc-seo-intelligence`), so sessions cannot be replayed between Research and SEO Studio.
- Only SC Identity sign-in methods are accepted.
- Subjects must be on the `--allow` list.

Workspace issues sessions from `POST /v1/seo-access/session`, which is a separate Workspace PR.

## Staging deployment (`deploy/staging/`)
The store is SQLite (the only backend the code implements). The PostgreSQL DDL in `contracts/sql/` is the
schema contract for a later move if volume demands it. Staging needs no new database server: the full
baseline is **27 MB**.

| Safeguard | How staging implements it | Rehearsed |
| --- | --- | --- |
| Isolated store | Dedicated named volume `sc-seo-staging-evidence`, not shared with any other service or database | yes |
| Writer role | `seo-import`: one-shot, started by hand, `network_mode: none`, the only container with a read-write mount | yes |
| Read-only role | `seo-studio`: volume mounted `:ro`, SQLite opened `mode=ro`, server has no write paths | yes (write attempt refused) |
| Append-only | Triggers refuse UPDATE and DELETE on every table, for the writer too | yes |
| Container hardening | Read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, non-root uid 10001, pids 64 | yes |
| Memory and CPU | Studio 256 MiB / 0.5 CPU (measured 30 MiB); importer 512 MiB / 1 CPU; no swap | yes |
| Storage | Image 50 MB; store 27 MB per baseline; each later run adds roughly its history size | measured |
| Access | Published on `127.0.0.1` only; reached through the TLS reverse proxy on a `*.sustainablecatalyst.com` host, so the Workspace cookie is same-site | proxy route needs owner approval |
| Identity | Workspace `sc-workspace-seo-session/1.0`; `--allow` lists Studio users; `--session-method erw-account-session` matches production Workspace sign-in | yes (refusals) |
| Secrets | Only the Workspace **public** keyring, mounted read-only. No private keys, database passwords or Google credentials | yes |
| Backups | Add a `ct-seo-evidence` stream (kind `sqlite-consistent tar`) to the existing Pi off-host backup: encrypted with age on the Mac, ciphertext on the Pi | backup and restore drill rehearsed; stream not added |
| Rollback | Immutable image tag; redeploy the previous tag. A bad run is superseded by a new run, never edited. Deleting the volume affects nothing else | — |
| No schedules | No cron, timers or crawls. Imports and Search Console pulls are run by hand | yes |

### Rehearsal
`SEO_STAGING_EVIDENCE=<private dir> PY=<python with cryptography> deploy/staging/rehearse.sh`

This builds the image, imports the real baseline into a throwaway volume, starts the hardened Studio, and runs
22 acceptance checks plus a backup and restore drill. Everything is removed afterwards.

### Staging procedure (each step needs owner approval)
1. A Workspace build containing `/v1/seo-access/session` is deployed where the Studio's users sign in, with
   `SC_WORKSPACE_SEO_STUDIO_SESSION_ENABLED=true`, `SC_WORKSPACE_SEO_STUDIO_USERS=<owner key>`, and the Studio
   origin in `SC_WORKSPACE_CORS_ALLOWED_ORIGINS`.
2. Build the image at a `main` commit and tag it immutably.
3. Copy the private SC-SEO-01 evidence to a mode-0700 directory on the host.
4. Run `deploy/staging/import-baseline.sh <UTC>` and record the history SHA-256. It must match the development
   value, `417b195b…`.
5. Start `seo-studio` and add the proxy route.
6. Add the backup stream and run one restore drill.
7. Owner browser acceptance: the checks in `rehearse.sh` (sign-in, refusals, counts, headers), carried out
   through the real Workspace sign-in.
