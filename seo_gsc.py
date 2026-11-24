"""Read-only Google Search Console connector (prepared, inactive until the owner authorizes it).

Design limits, enforced in code:

* **One scope**: ``https://www.googleapis.com/auth/webmasters.readonly``. Any other scope is refused.
* **Two methods**: Search Analytics ``query`` and URL Inspection ``index:inspect``, both read-only. Any other endpoint
  is refused before a request is made (sitemap submission, property changes, user management are not reachable).
* **One property**: the pinned property (``sc-domain:`` or URL-prefix); a request for another property is refused.
* **Own credential**: a dedicated service account added to the property as a *Restricted* user. The key is read from
  a server-side file named by ``SEO_GSC_SERVICE_ACCOUNT_FILE`` (mode 0600, never in the repository, never logged).
  WordPress administrator OAuth tokens (Site Kit, Rank Math) are never read or reused.
* **Bounded**: a run inspects at most ``--max-inspections`` URLs (daily API quota is 2,000 per property); no
  scheduling here — a recurring pull is a separate, authorized deployment.

Output is a ``seo.evidence-history.v1`` document with ``gsc_api`` observations (impressions, clicks, CTR, position;
coverage state, verdict, Google-selected canonical, last crawl time as reported by Google). The connector never
infers indexing: a URL Google has not reported on stays unknown.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from urllib.parse import quote
import urllib.request

from seo_evidence import EvidenceHistory, utc_timestamp

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEARCH_ANALYTICS = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
URL_INSPECTION = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
ALLOWED_ENDPOINTS = frozenset({TOKEN_URL, URL_INSPECTION})
ROW_LIMIT = 25000
MAX_INSPECTIONS = 2000
KEY_ENV = "SEO_GSC_SERVICE_ACCOUNT_FILE"
CONNECTOR_VERSION = "seo_gsc/1.0.0"


class Refused(RuntimeError):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_service_account(path):
    """Read a service-account key; refuse group/world-readable files. Returns only what signing needs."""
    path = Path(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise Refused("The service-account key file must not be readable by group or others (chmod 600)")
    data = json.loads(path.read_bytes())
    if data.get("type") != "service_account":
        raise Refused("The credential is not a service-account key; administrator OAuth tokens are not accepted")
    return {"client_email": data["client_email"], "private_key": data["private_key"],
            "token_uri": data.get("token_uri", TOKEN_URL)}


class Client:
    def __init__(self, property_id, transport=None, signer=None, scope=SCOPE):
        if scope != SCOPE:
            raise Refused("Only the read-only Search Console scope is permitted")
        if not (property_id.startswith("sc-domain:") or property_id.startswith("https://")):
            raise Refused("Pin a Search Console property (sc-domain:example.com or https://example.com/)")
        self.property = property_id
        self.transport = transport or self._urllib
        self.signer = signer
        self.token = None
        self.requests = 0

    @staticmethod
    def _urllib(url, body, headers):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - endpoint allowlisted
            return json.loads(resp.read())

    def _post(self, url, payload, form=False):
        allowed = url in ALLOWED_ENDPOINTS or url == SEARCH_ANALYTICS.format(site=quote(self.property, safe=""))
        if not allowed:
            raise Refused("Endpoint not on the read-only allowlist")
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        if url != TOKEN_URL:
            headers["Authorization"] = "Bearer " + self._access_token()
        body = payload.encode() if form else json.dumps(payload).encode()
        self.requests += 1
        return self.transport(url, body, headers)

    def _access_token(self):
        if self.token and self.token[1] > time.time() + 60:
            return self.token[0]
        if self.signer is None:
            raise Refused("No credential configured; the connector stays inactive until the owner provisions one")
        now = int(time.time())
        claims = {"iss": self.signer["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
        assertion = self.signer["sign"](claims)
        reply = self._post(TOKEN_URL, "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion="
                           + assertion, form=True)
        granted = set(str(reply.get("scope", SCOPE)).split())
        if granted - {SCOPE}:
            raise Refused("Google granted scopes beyond read-only Search Console; refusing to continue")
        self.token = (reply["access_token"], now + int(reply.get("expires_in", 3600)))
        return self.token[0]

    def check_property(self, site):
        if site != self.property:
            raise Refused("Requests are pinned to the configured Search Console property")

    def search_analytics(self, start_date, end_date, search_type="web"):
        rows, start = [], 0
        url = SEARCH_ANALYTICS.format(site=quote(self.property, safe=""))
        while True:
            reply = self._post(url, {"startDate": start_date, "endDate": end_date, "dimensions": ["page"],
                                     "type": search_type, "rowLimit": ROW_LIMIT, "startRow": start,
                                     "dataState": "final"})
            batch = reply.get("rows") or []
            rows.extend(batch)
            if len(batch) < ROW_LIMIT:
                return rows, reply.get("responseAggregationType")
            start += ROW_LIMIT

    def inspect(self, url):
        return self._post(URL_INSPECTION, {"inspectionUrl": url, "siteUrl": self.property})


def rs256_signer(account):
    """Build a JWT signer from a service-account key (requires the optional ``cryptography`` package)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)

    def sign(claims):
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps(claims, separators=(",", ":")).encode())
        sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(sig)}"
    return {"client_email": account["client_email"], "sign": sign}


def collect(client, scope, start_date, end_date, inspect_urls=(), pulled_at=None):
    """Pull read-only evidence into an evidence history. Returns (history_dict, receipt)."""
    pulled_at = utc_timestamp(pulled_at)
    evidence = EvidenceHistory(scope, pulled_at)
    rows, aggregation = client.search_analytics(start_date, end_date)
    raw = json.dumps({"rows": rows}, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    evidence.artifacts[("gsc_api", digest)] = {"source_kind": "gsc_api", "source_file": "searchAnalytics.query",
                                               "source_sha256": digest, "imported_at_utc": pulled_at,
                                               "property": client.property, "range": [start_date, end_date],
                                               "aggregation": aggregation, "connector": CONNECTOR_VERSION}
    performance = []
    for i, row in enumerate(rows):
        url = row["keys"][0]
        facts = {"impressions": int(row.get("impressions", 0)), "clicks": int(row.get("clicks", 0)),
                 "ctr": row.get("ctr"), "position": row.get("position"), "range": [start_date, end_date],
                 "aggregation": aggregation}
        evidence.add("gsc_api", digest, i, url, facts, pulled_at)
        performance.append({"engine": "google", "url": url, "start_date": start_date, "end_date": end_date,
                            "clicks": facts["clicks"], "impressions": facts["impressions"], "ctr": facts["ctr"],
                            "position": facts["position"]})
    inspected = []
    for i, url in enumerate(list(inspect_urls)[:MAX_INSPECTIONS]):
        result = (client.inspect(url).get("inspectionResult") or {}).get("indexStatusResult") or {}
        inspected.append({"url": url, "result": result})
        evidence.add("gsc_api", "inspection:" + digest, i, url, {
            "coverage_state": result.get("coverageState"), "verdict": result.get("verdict"),
            "indexing_state": result.get("indexingState"), "google_canonical": result.get("googleCanonical"),
            "user_canonical": result.get("userCanonical"), "last_crawl_time_raw": result.get("lastCrawlTime"),
            "robots_txt_state": result.get("robotsTxtState"), "page_fetch_state": result.get("pageFetchState")},
            pulled_at)
    if inspected:
        idigest = hashlib.sha256(json.dumps(inspected, sort_keys=True).encode()).hexdigest()
        evidence.artifacts[("gsc_api", idigest)] = {"source_kind": "gsc_api", "source_file": "urlInspection.index.inspect",
                                                    "source_sha256": idigest, "imported_at_utc": pulled_at,
                                                    "property": client.property, "connector": CONNECTOR_VERSION}
    evidence.extensions["search_performance"] = performance
    history = evidence.build()
    receipt = {"connector": CONNECTOR_VERSION, "property": client.property, "scope": SCOPE, "pulled_at_utc": pulled_at,
               "requests": client.requests, "performance_rows": len(rows), "inspections": len(inspected)}
    return history, receipt


def main(argv=None):
    cli = argparse.ArgumentParser(description="Read-only Search Console pull (manual; owner-authorized runs only)")
    cli.add_argument("--property", required=True, help="Pinned property, e.g. sc-domain:example.com")
    cli.add_argument("--scope", required=True, help="Evidence property scope, e.g. https://example.com/")
    cli.add_argument("--start-date", required=True)
    cli.add_argument("--end-date", required=True)
    cli.add_argument("--inspect-list", type=Path, help="Text file of URLs to inspect (bounded)")
    cli.add_argument("--max-inspections", type=int, default=200)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--authorized-by", required=True,
                     help="Reference to the owner authorization for this pull (recorded in the receipt)")
    args = cli.parse_args(argv)
    key_file = os.environ.get(KEY_ENV)
    if not key_file:
        print(f"{KEY_ENV} is not set; no credential, nothing requested.", file=sys.stderr)
        return 2
    client = Client(args.property, signer=rs256_signer(load_service_account(key_file)))
    urls = [u.strip() for u in args.inspect_list.read_text().splitlines() if u.strip()] if args.inspect_list else []
    history, receipt = collect(client, args.scope, args.start_date, args.end_date,
                               urls[:min(args.max_inspections, MAX_INSPECTIONS)])
    receipt["authorized_by"] = args.authorized_by
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(json.dumps({"evidence_history": history, "receipt": receipt}, indent=2, sort_keys=True))
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
