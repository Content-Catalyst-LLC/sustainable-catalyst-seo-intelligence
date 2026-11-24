# SEO Studio staging kit
- `Dockerfile`: one image for the read-only Studio and the offline importer, built from this repository with no evidence inside.
- `compose.yml`: the hardened `seo-studio` reader and the `seo-import` writer, which you start by hand.
- `import-baseline.sh`: imports the SC-SEO-01 baseline into the evidence volume.
- `rehearse.sh`: a local end-to-end rehearsal with 22 checks plus a restore drill.

See `docs/SEO-STUDIO.md` for the safeguards and the staging procedure. Nothing here schedules, crawls or calls Google.
