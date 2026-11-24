"""Verify Workspace-signed SEO Studio sessions (reuses the Workspace identity design used by Research).

Workspace signs a short-lived ``sc-workspace-seo-session/1.0`` document for a person signed in to Sustainable
Catalyst. The signature is Ed25519 over ``schema + "\\n" + canonical JSON`` of the body without ``signature``,
the same construction as ``sc-workspace-research-session/1.0`` (Workspace ``research_exports.sign_document``;
Research ``workspace_ingest.verify_document``). Only the schema and audience differ, so a Research session can never
be replayed here and vice versa. The browser sends ``Authorization: SC-Workspace <base64url(document)>``.

Checks (fail closed): current key from the pinned keyring; issuer and audience; SC Identity sign-in method (or an
explicitly configured extra method); subject matches the pattern and is on the Studio allowlist; issued no more than
60 s ahead, valid for at most 300 s, unexpired.
"""
import base64
import json
import re
from datetime import datetime, timezone

SESSION_SCHEMA = "sc-workspace-seo-session/1.0"
ISSUER = "sustainable-catalyst-workspace"
AUDIENCE = "sc-seo-intelligence"
SCHEME = "SC-Workspace"
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 60
MAX_HEADER_BYTES = 8192
SC_IDENTITY_METHODS = frozenset({"sc-identity-oidc-session", "sc-identity-access-token"})
CONFIGURABLE_METHODS = frozenset({"erw-account-session"})
SUBJECT = re.compile(r"^workspace-user:[A-Za-z0-9._:@-]{1,128}$")


class SessionRefused(PermissionError):
    """Raised with a message that is safe to show to the person."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64d(value):
    value = value.strip()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Keyring:
    """Pinned Workspace public keys: {"keys": [{"keyId", "publicKey" (base64 raw 32 bytes), "state"}]}."""

    def __init__(self, data):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        self.keys = {}
        for item in data.get("keys", []):
            raw = base64.b64decode(item["publicKey"])
            if len(raw) != 32:
                raise ValueError("Workspace public keys must be raw 32-byte Ed25519 keys")
            self.keys[item["keyId"]] = (Ed25519PublicKey.from_public_bytes(raw), item.get("state", "current"))
        if not any(state == "current" for _, state in self.keys.values()):
            raise ValueError("The keyring has no current Workspace key")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as stream:
            return cls(json.loads(stream.read()))

    def current(self, key_id):
        key, state = self.keys.get(key_id, (None, None))
        if key is None or state != "current":
            raise SessionRefused("The Sustainable Catalyst session was signed by an unknown or retired key")
        return key


def verify_session(authorization, keyring, allowed_subjects, methods=(), now=None):
    """Return the verified session body or raise SessionRefused."""
    from cryptography.exceptions import InvalidSignature
    scheme, _, value = (authorization or "").strip().partition(" ")
    if scheme != SCHEME or not value.strip():
        raise SessionRefused("Sign in to Sustainable Catalyst to open SEO Studio")
    if len(value) > MAX_HEADER_BYTES:
        raise SessionRefused("The Sustainable Catalyst session is malformed")
    try:
        doc = json.loads(_b64d(value))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SessionRefused("The Sustainable Catalyst session is malformed") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SESSION_SCHEMA:
        raise SessionRefused("The Sustainable Catalyst session is not an SEO Studio session")
    signature = doc.get("signature") or {}
    body = {k: v for k, v in doc.items() if k != "signature"}
    key = keyring.current(signature.get("keyId"))
    try:
        key.verify(_b64d(str(signature.get("value") or "")), SESSION_SCHEMA.encode() + b"\n" + canonical(body))
    except (InvalidSignature, ValueError) as exc:
        raise SessionRefused("The Sustainable Catalyst session could not be verified") from exc
    if body.get("issuer") != ISSUER or body.get("audience") != AUDIENCE:
        raise SessionRefused("The session was issued for a different service")
    subject = str(body.get("subject") or "")
    if not SUBJECT.fullmatch(subject):
        raise SessionRefused("The Sustainable Catalyst session names no Workspace person")
    if subject not in allowed_subjects:
        raise SessionRefused("This person is not authorized for SEO Studio")
    accepted = SC_IDENTITY_METHODS | (frozenset(methods) & CONFIGURABLE_METHODS)
    if body.get("authentication") not in accepted:
        raise SessionRefused("SEO Studio needs a Sustainable Catalyst sign-in, not a service credential")
    now = now or datetime.now(timezone.utc)
    try:
        issued = datetime.fromisoformat(str(body["issuedAt"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(body["expiresAt"]).replace("Z", "+00:00"))
        if issued.tzinfo is None or expires.tzinfo is None:
            raise ValueError
    except (KeyError, ValueError) as exc:
        raise SessionRefused("The Sustainable Catalyst session has no valid issue time or expiry") from exc
    if (issued - now).total_seconds() > CLOCK_SKEW_SECONDS or \
            (expires - issued).total_seconds() > MAX_LIFETIME_SECONDS + CLOCK_SKEW_SECONDS:
        raise SessionRefused("The Sustainable Catalyst session is dated in the future or valid for too long")
    if expires <= now:
        raise SessionRefused("The Sustainable Catalyst session has expired; sign in again")
    return body
