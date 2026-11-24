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

## Infrastructure safeguards before staging or production storage
None of these exist yet. Each needs owner authorization.

1. **Isolation**
   - A dedicated database `seo_intelligence` on the shared PostgreSQL server, or a separate small instance.
   - Roles `seo_owner`, `seo_writer` (INSERT/SELECT) and `seo_reader` (SELECT).
   - `REVOKE ALL FROM PUBLIC`; reachable only on the internal Docker network.
2. **Secrets**
   - Database passwords, the Workspace public keyring and any GSC key live in server-side files with mode 0600 or in Docker secrets.
   - None of them are in Git, in the image or in logs.
3. **Backups**
   - A nightly `pg_dump -Fc` of the SEO database, encrypted.
   - Off-host copy (the existing off-site target used for Workspace backups), with 30 daily and 12 monthly copies retained.
4. **Verified restore**
   - Before go-live, restore the latest dump into a scratch database and compare row counts and `history_sha256` for every run. Record the receipt.
   - Repeat monthly.
5. **Monitoring**
   - Container memory limit of 256 MiB for the server and 512 MiB for the importer.
   - Disk alert when the host passes 85% (it is at about 84% now).
   - Database size reported per run.
   - Health endpoint on the proxy.
6. **Rollback**
   - Studio is a separate container with an immutable image tag. Rolling back means redeploying the previous tag.
   - A bad run is never edited. A superseding run is stored, and the dashboard defaults to the latest run.
   - Dropping the SEO database affects nothing else.
7. **No schedules**
   - Collectors, verification runs and GSC pulls are started manually.
   - A recurring crawl or API pull needs separate authorization, with the agreed crawl policy (1 request per 3 seconds, single concurrency).

## Staging acceptance procedure
1. Deploy the Workspace session endpoint and SEO Studio to staging with an allowlist of only the owner's subject.
2. Signed out, `/api/runs` returns 401.
3. Signed in, the run list loads.
4. These are refused with 401:
   - another subject;
   - an expired session;
   - a Research session;
   - a tampered session.
5. Import the baseline on staging and confirm the receipt shows `reconciled: true` with 9/9 checks.
6. Check that the history SHA-256 matches the development import.
7. Compare dashboard counts with the receipt:
   - status 200/404/unknown = 3,301 / 448 / 2;
   - broken edges 2,694;
   - sitemap URLs 2,232;
   - KB not_verified 324 / verified 1.
8. Every batch shows `validated-not-deployed`.
9. Restore drill: restore the dump into a scratch database and confirm identical run hashes and row counts.
10. Check headers: CSP, `no-store`, `X-Frame-Options: DENY`, and no external assets.
11. Confirm that no outbound requests were made during the session, from proxy and egress logs.
