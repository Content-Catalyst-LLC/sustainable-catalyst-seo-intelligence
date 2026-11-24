#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $# != 1 || -e "$1" ]]; then
    echo "Usage: $0 <new-output-directory>" >&2
    exit 2
fi
case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) platform=macos-arm64; digest=6b893a15ce695a002bbe6c3d2467954fee64059216d471fae55c781936d96e2f ;;
    Darwin/x86_64) platform=macos-x64; digest=724639bdfca7d26514a7c3f10caa6a9172a4f2192744d9e4691bcf842b2e4609 ;;
    Linux/x86_64) platform=linux-x64; digest=09278d958d4a087fa46093805cd33b085b96618001dd31d45c448ad724c9024e ;;
    *) echo "Unsupported checksum-pinned platform" >&2; exit 2 ;;
esac
mkdir "$1"
cd "$1"
mkdir empty http-cache
asset="siteone-crawler-v2.5.1-$platform.tar.gz"
release=https://github.com/janreges/siteone-crawler/releases/download/v2.5.1
curl --max-time 180 -fsSL "$release/$asset" -o "$asset"
printf '%s  %s\n' "$digest" "$asset" | shasum -a 256 -c
curl --max-time 30 -fsSL https://raw.githubusercontent.com/janreges/siteone-crawler/v2.5.1/LICENSE -o upstream-LICENSE
tar -xzf "$asset"
cmp upstream-LICENSE siteone-crawler/LICENSE

origin=http://127.0.0.1:18765
python3 "$root/scripts/siteone_fixture.py" >server.log 2>&1 &
fixture=$!
trap 'kill "$fixture" 2>/dev/null || true' EXIT
for attempt in {1..25}; do
    kill -0 "$fixture"
    if curl --noproxy '*' -fsS "$origin/robots.txt" -o robots.txt 2>/dev/null; then break; fi
    sleep .2
done
kill -0 "$fixture"
grep -q 'Disallow: /denied' robots.txt

env -i PATH=/usr/bin:/bin TZ=UTC ./siteone-crawler/siteone-crawler \
    --url="$origin/" --user-agent=Sustainable-Catalyst-SEO-Acceptance \
    --workers=1 --max-reqs-per-sec=1 --max-depth=1 --max-visited-urls=12 \
    --max-queue-length=16 --timeout=3 --http-cache-dir="$PWD/http-cache" --output-html-report= \
    --output-json-file=siteone.json >crawl.log 2>&1
if grep -q '"GET /denied ' server.log; then
    echo "robots-disallowed route was requested" >&2
    exit 1
fi
curl --noproxy '*' -fsS "$origin/noindex" -D noindex.headers -o noindex.html
curl --noproxy '*' -fsS "$origin/sitemap.xml" -o sitemap.xml

imported_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n' "$imported_at" > imported-at.txt
for run in first replay; do
    python3 "$root/seo_siteone.py" --property "$origin/" --siteone-json siteone.json \
        --imported-at "$imported_at" --output-dir "$run"
    python3 "$root/seo_studio.py" --input-dir empty --property "$origin/" \
        --siteone-json siteone.json --sitemap-xml sitemap.xml --html-response "$origin/noindex" noindex.html \
        --imported-at "$imported_at" --output-dir "$run"
done
diff -r first replay
python3 "$root/scripts/check_siteone_acceptance.py" "$digest"
