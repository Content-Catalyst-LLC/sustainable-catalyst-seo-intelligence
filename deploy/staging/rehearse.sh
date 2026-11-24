#!/usr/bin/env bash
# Local staging rehearsal (developer machine with Docker). Runs the same image, compose hardening, import and
# acceptance checks as staging, using a throwaway in-memory Workspace key instead of the real one.
# Usage: SEO_STAGING_EVIDENCE=<private SC-SEO-01 dir> deploy/staging/rehearse.sh
# Creates and then removes the compose project "sc-seo-rehearsal" and its volumes. Never touches remote hosts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/deploy/staging"
: "${SEO_STAGING_EVIDENCE:?private evidence root}"
PY="${PY:-python3}"   # needs the cryptography package
WORK="$(mktemp -d)"
export COMPOSE_PROJECT_NAME=sc-seo-rehearsal SEO_IMAGE_TAG=rehearsal SEO_STAGING_PORT=18982
export SEO_STAGING_ALLOW=workspace-user:owner SEO_STAGING_SESSION_URL=https://workspace.example.invalid/v1/seo-access/session
export SEO_STAGING_KEYRING="$WORK/keyring.json"
dc() { docker compose -f compose.yml -p sc-seo-rehearsal "$@"; }
cleanup() { dc --profile import down -v --remove-orphans >/dev/null 2>&1 || true; docker volume rm -f sc-seo-staging-evidence-restored >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT
PASS=0; FAIL=0
check() { if eval "$2"; then echo "PASS $1"; PASS=$((PASS+1)); else echo "FAIL $1"; FAIL=$((FAIL+1)); fi; }

docker build -q -t sc-seo-intelligence:rehearsal -f Dockerfile "$ROOT" >/dev/null
# Throwaway signing key: private half stays in this process's temp dir, keyring holds only the public key.
"$PY" - "$WORK" <<'PY'
import base64, hashlib, json, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
k = Ed25519PrivateKey.generate(); raw = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
json.dump({"keys": [{"keyId": hashlib.sha256(raw).hexdigest()[:16], "publicKey": base64.b64encode(raw).decode(),
                     "state": "current"}]}, open(sys.argv[1] + "/keyring.json", "w"))
open(sys.argv[1] + "/key.raw", "wb").write(k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
PY
chmod 0644 "$WORK/keyring.json"

# 1. Import (writer, offline) -------------------------------------------------------------------------
docker volume rm -f sc-seo-staging-evidence >/dev/null 2>&1 || true
./import-baseline.sh 2026-11-24T14:00:00Z > "$WORK/import.log" 2>&1 || { cat "$WORK/import.log"; exit 1; }
HIST=$(awk '/seo-baseline-history.json/{print $1}' "$WORK/import.log")
check "import reproduces baseline history sha256 (417b195b…)" '[[ "$HIST" == 417b195b2badb3d8* ]]'
# seo_baseline exits 2 on any reconciliation mismatch, and the import script stops on that exit.

# 2. Studio (reader, hardened) -------------------------------------------------------------------------
dc up -d seo-studio >/dev/null
for _ in $(seq 1 30); do curl -fs "http://127.0.0.1:18982/app.css" >/dev/null && break; sleep 1; done
B=http://127.0.0.1:18982
"$PY" - "$WORK" "$B" > "$WORK/acceptance.txt" <<'PY'
import base64, json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
work, base = sys.argv[1], sys.argv[2]
key = Ed25519PrivateKey.from_private_bytes(open(work + "/key.raw", "rb").read())
kid = json.load(open(work + "/keyring.json"))["keys"][0]["keyId"]
canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def sess(schema="sc-workspace-seo-session/1.0", aud="sc-seo-intelligence", sub="workspace-user:owner",
         method="erw-account-session", life=300, shift=0):
    now = datetime.now(timezone.utc) + timedelta(seconds=shift)
    body = {"schema": schema, "issuer": "sustainable-catalyst-workspace", "audience": aud, "subject": sub,
            "authentication": method, "issuedAt": now.isoformat(), "expiresAt": (now + timedelta(seconds=life)).isoformat()}
    sig = base64.urlsafe_b64encode(key.sign(schema.encode() + b"\n" + canon(body))).rstrip(b"=").decode()
    doc = {**body, "signature": {"alg": "Ed25519", "keyId": kid, "value": sig}}
    return "SC-Workspace " + base64.urlsafe_b64encode(canon(doc)).rstrip(b"=").decode()
def get(path, auth=None, method="GET"):
    req = urllib.request.Request(base + path, headers={"Authorization": auth} if auth else {}, method=method)
    try:
        with urllib.request.urlopen(req) as r: return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e: return e.code, e.read(), e.headers
out = {}
out["unauthenticated_401"] = get("/api/runs")[0] == 401
refusals = {"research_session": sess(schema="sc-workspace-research-session/1.0", aud="sc-research"),
            "other_subject": sess(sub="workspace-user:someone"), "expired": sess(shift=-700),
            "overlong": sess(life=3600), "service_method": sess(method="service-bearer")}
for name, a in refusals.items(): out["refuse_" + name] = get("/api/runs", a)[0] == 401
ok = sess()
st, body, hdr = get("/api/runs", ok); runs = json.loads(body)["runs"]
out["owner_200"] = st == 200 and len(runs) == 1
o = json.loads(get("/api/overview?run=" + runs[0]["run_id"], ok)[1])
out["status_3301_448_2"] = o["technical"]["status"] == {"200": 3301, "404": 448, "unknown": 2}
out["broken_edges_2694"] = o["broken_links"]["edges"] == 2694
out["sitemap_urls_2232"] = (o["sitemaps"]["summary"] or {}).get("unique_urls") == 2232
out["kb_324_1"] = o["knowledge_base"]["verification_states"] == {"not_verified": 324, "verified": 1}
out["batches_not_deployed"] = {e["state"] for e in o["change_events"]} == {"validated-not-deployed"}
out["reconciled_9_of_9"] = sum(c["match"] for c in o["provenance"]["reconciliation"]) == 9
out["no_indexing_inferred"] = o["indexing"]["pages_with_google_evidence"] == 0 and bool(o["indexing"]["note"])
out["recommendations_inferred"] = sum(r["n"] for r in o["recommendations"]) == 1001
h = get("/", None)[2]
out["headers"] = "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["Cache-Control"] == "no-store" \
    and h["X-Frame-Options"] == "DENY"
out["post_refused"] = get("/api/runs", ok, "POST")[0] in (404, 501)
print(json.dumps(out))
PY
for k in $(python3 -c "import json;print(' '.join(json.load(open('$WORK/acceptance.txt'))))"); do
  check "studio $k" "python3 -c \"import json,sys;sys.exit(0 if json.load(open('$WORK/acceptance.txt'))['$k'] else 1)\""
done
CID=$(dc ps -q seo-studio)
echo "studio memory after acceptance: $(docker stats --no-stream --format "{{.MemUsage}}" "$CID")"
echo "image size: $(docker image inspect -f "{{.Size}}" sc-seo-intelligence:rehearsal) bytes"
check "studio container read-only rootfs, no caps, non-root, 256 MiB" \
  '[[ "$(docker inspect -f "{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.Config.User}} {{.HostConfig.Memory}}" $CID)" == "true [ALL] 10001:10001 268435456" ]]'
check "studio cannot write the evidence volume" '! docker exec "$CID" sh -c "echo x >> /data/seo-evidence.sqlite" 2>/dev/null'
check "append-only store refuses DELETE even for the writer" \
  '! dc --profile import run --rm --entrypoint python seo-import -c "import sqlite3;sqlite3.connect(\"/data/seo-evidence.sqlite\").execute(\"DELETE FROM finding\")" >/dev/null 2>&1'

# 3. Backup and restore drill --------------------------------------------------------------------------
dc --profile import run --rm --entrypoint python seo-import -c \
  "import sqlite3;s=sqlite3.connect('file:/data/seo-evidence.sqlite?mode=ro',uri=True);d=sqlite3.connect('/tmp/b.sqlite');s.backup(d);d.close();import sys;sys.stdout.buffer.write(open('/tmp/b.sqlite','rb').read())" \
  > "$WORK/backup.sqlite"
docker volume create sc-seo-staging-evidence-restored >/dev/null
docker run --rm -i --network none --user 10001:10001 -v sc-seo-staging-evidence-restored:/data --entrypoint sh \
  sc-seo-intelligence:rehearsal -c 'cat > /data/seo-evidence.sqlite' < "$WORK/backup.sqlite"
Q='import sqlite3,json;d=sqlite3.connect("file:/data/seo-evidence.sqlite?mode=ro",uri=True);print(json.dumps({t:d.execute(f"select count(*) from {t}").fetchone()[0] for t in ("run","artifact","observation","page","finding","change_event","recommendation")}|{"hash":d.execute("select history_sha256 from run").fetchall()}))'
ORIG=$(dc --profile import run --rm --entrypoint python seo-import -c "$Q")
REST=$(docker run --rm --network none --user 10001:10001 -v sc-seo-staging-evidence-restored:/data:ro --entrypoint python sc-seo-intelligence:rehearsal -c "$Q")
check "restore drill: identical row counts and run hashes ($ORIG)" '[[ "$ORIG" == "$REST" ]]'
echo "backup bytes: $(wc -c < "$WORK/backup.sqlite")"
echo "RESULT pass=$PASS fail=$FAIL"
[[ $FAIL -eq 0 ]]
