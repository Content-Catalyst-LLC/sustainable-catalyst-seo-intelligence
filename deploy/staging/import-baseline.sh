#!/usr/bin/env bash
# Import the SC-SEO-01 baseline into the staging evidence store (writer, offline, by hand).
# Usage: SEO_IMAGE_TAG=<tag> SEO_STAGING_EVIDENCE=<private dir> ... deploy/staging/import-baseline.sh <imported-at UTC>
# <private dir> must contain evidence/, release/, tools/ and deliverables/SC-SEO-01-ISSUES.csv (never in Git).
set -euo pipefail
cd "$(dirname "$0")"
AT="${1:?imported-at UTC timestamp, e.g. 2026-11-24T14:00:00Z}"
dc() { docker compose -f compose.yml --profile import "$@"; }
dc run --rm --entrypoint sh seo-import -c '
  set -eu
  test ! -e /data/seo-evidence.sqlite || { echo "store exists: append a new run with seo_store.py instead" >&2; exit 2; }
  python -s -E seo_baseline.py --evidence-dir /evidence/evidence --release-dir /evidence/release \
    --tool-dir /evidence/tools --issue-groups /evidence/deliverables/SC-SEO-01-ISSUES.csv \
    --output-dir /tmp/baseline --property https://sustainablecatalyst.com/ --imported-at "'"$AT"'"
  python -s -E seo_store.py --db /data/seo-evidence.sqlite --history /tmp/baseline/seo-baseline-history.json \
    --receipt /tmp/baseline/seo-baseline-receipt.json --label "SC-SEO-01 sitewide baseline (2026-11-23)" \
    --kind baseline --with-recommendations
  sha256sum /tmp/baseline/seo-baseline-history.json /tmp/baseline/seo-baseline-receipt.json'
