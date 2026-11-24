"""DEVELOPMENT ONLY: a local stand-in for the Workspace SEO session endpoint.

Generates a throwaway Ed25519 key in memory (never written to disk), writes the matching public keyring for
``seo_studio_server.py --keyring``, and serves ``POST /v1/seo-access/session`` on loopback, returning a signed
``sc-workspace-seo-session/1.0`` document for one configured subject. It performs no sign-in: it exists so the
Studio can be exercised before the Workspace endpoint (separate PR) is deployed. Refuses to bind off loopback.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seo_workspace_auth import AUDIENCE, ISSUER, SESSION_SCHEMA, canonical  # noqa: E402


def make_signer():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = hashlib.sha256(raw).hexdigest()[:16]
    return key, key_id, {"keys": [{"keyId": key_id, "publicKey": base64.b64encode(raw).decode(), "state": "current"}]}


def signed_document(key, key_id, subject, method="sc-identity-oidc-session", lifetime=300, now=None):
    """The document exactly as Workspace's POST /v1/seo-access/session returns it."""
    now = now or datetime.now(timezone.utc)
    body = {"schema": SESSION_SCHEMA, "issuer": ISSUER, "audience": AUDIENCE, "subject": subject,
            "authentication": method, "issuedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": (now + timedelta(seconds=lifetime)).isoformat().replace("+00:00", "Z")}
    sig = key.sign(SESSION_SCHEMA.encode() + b"\n" + canonical(body))
    return {**body, "signature": {"alg": "Ed25519", "keyId": key_id,
                                  "value": base64.urlsafe_b64encode(sig).rstrip(b"=").decode()}}


def issue(key, key_id, subject, **kw):
    """The Authorization header value the Studio page builds from that document."""
    doc = signed_document(key, key_id, subject, **kw)
    return "SC-Workspace " + base64.urlsafe_b64encode(canonical(doc)).rstrip(b"=").decode()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--subject", required=True)
    cli.add_argument("--keyring-out", required=True, type=Path)
    cli.add_argument("--studio-origin", default="http://127.0.0.1:18980")
    cli.add_argument("--port", type=int, default=18990)
    args = cli.parse_args()
    key, key_id, keyring = make_signer()
    args.keyring_out.write_text(json.dumps(keyring, indent=2))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def cors(self):
            self.send_header("Access-Control-Allow-Origin", args.studio_origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Accept")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_POST(self):  # noqa: N802
            if self.path != "/v1/seo-access/session":
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(signed_document(key, key_id, args.subject)).encode()
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    print(f"DEV session issuer on http://127.0.0.1:{args.port}/v1/seo-access/session (key {key_id}, in memory)")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
