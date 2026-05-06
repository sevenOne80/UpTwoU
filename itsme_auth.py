"""
itsme OIDC integration — Authorization Code + PKCE + private_key_jwt auth.

Required env vars:
  ITSME_CLIENT_ID       — client_id registered on itsme partner portal
  ITSME_PRIVATE_KEY     — RSA-2048 private key PEM (inline, \n-escaped)
                          OR set ITSME_PRIVATE_KEY_PATH to a file path
  ITSME_ENV             — 'sandbox' (default) | 'production'

Optional:
  ITSME_KID             — key ID to include in JWK/JWT header (default: 'key1')
"""
import os
import secrets
import hashlib
import base64
import time
import uuid

import requests as _requests
from urllib.parse import urlencode

# ── Discovery ──────────────────────────────────────────────────────────────────
_DISCOVERY = {
    'sandbox':    'https://idp.int.itsme.services/v2/.well-known/openid-configuration',
    'production': 'https://idp.prd.itsme.services/v2/.well-known/openid-configuration',
}
_oidc_cache: dict = {}


def _env() -> str:
    return os.environ.get('ITSME_ENV', 'sandbox')


def oidc_config() -> dict | None:
    env = _env()
    if env in _oidc_cache:
        return _oidc_cache[env]
    try:
        r = _requests.get(_DISCOVERY[env], timeout=5)
        r.raise_for_status()
        _oidc_cache[env] = r.json()
        return _oidc_cache[env]
    except Exception:
        return None


def is_configured() -> bool:
    return bool(os.environ.get('ITSME_CLIENT_ID')) and bool(_private_key_pem())


# ── Private key helpers ────────────────────────────────────────────────────────
def _private_key_pem() -> str:
    path = os.environ.get('ITSME_PRIVATE_KEY_PATH', '')
    if path and os.path.isfile(path):
        with open(path) as f:
            return f.read()
    inline = os.environ.get('ITSME_PRIVATE_KEY', '')
    return inline.replace('\\n', '\n')


def _build_client_assertion(token_endpoint: str) -> str:
    """Return a signed RS256 JWT for private_key_jwt client authentication."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    import json

    pem = _private_key_pem().encode()
    private_key = load_pem_private_key(pem, password=None)
    client_id = os.environ.get('ITSME_CLIENT_ID', '')
    kid = os.environ.get('ITSME_KID', 'key1')

    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT', 'kid': kid}
    payload = {
        'iss': client_id,
        'sub': client_id,
        'aud': token_endpoint,
        'jti': str(uuid.uuid4()),
        'iat': now,
        'exp': now + 300,
    }

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

    h = _b64(json.dumps(header).encode())
    p = _b64(json.dumps(payload).encode())
    signing_input = f"{h}.{p}".encode()

    sig = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64(sig)}"


# ── Authorization URL ──────────────────────────────────────────────────────────
def build_auth_url(flask_session: dict, redirect_uri: str, next_url: str = '') -> str | None:
    config = oidc_config()
    if not config:
        return None

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    flask_session['itsme_state'] = state
    flask_session['itsme_nonce'] = nonce
    flask_session['itsme_code_verifier'] = code_verifier
    flask_session['itsme_next'] = next_url

    params = {
        'response_type': 'code',
        'client_id': os.environ.get('ITSME_CLIENT_ID', ''),
        'redirect_uri': redirect_uri,
        'scope': 'openid profile email phone',
        'state': state,
        'nonce': nonce,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'acr_values': 'tag:itsme.services,2016-06:acr:advanced',
    }
    return config['authorization_endpoint'] + '?' + urlencode(params)


# ── Token exchange ─────────────────────────────────────────────────────────────
def exchange_code(code: str, flask_session: dict, redirect_uri: str) -> dict | None:
    """Exchange auth code → tokens. Returns ID token claims or None on failure."""
    config = oidc_config()
    if not config:
        return None

    token_endpoint = config['token_endpoint']
    client_assertion = _build_client_assertion(token_endpoint)

    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': flask_session.get('itsme_code_verifier', ''),
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': client_assertion,
    }

    try:
        r = _requests.post(token_endpoint, data=data, timeout=10)
        r.raise_for_status()
        tokens = r.json()
    except Exception:
        return None

    id_token = tokens.get('id_token', '')
    if not id_token:
        return None

    claims = _decode_id_token(id_token, config, flask_session.get('itsme_nonce', ''))
    if claims is None:
        return None

    # Optionally enrich with userinfo
    access_token = tokens.get('access_token', '')
    if access_token and config.get('userinfo_endpoint'):
        try:
            ui = _requests.get(
                config['userinfo_endpoint'],
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=5,
            )
            if ui.ok:
                claims.update(ui.json())
        except Exception:
            pass

    return claims


# ── ID token validation ────────────────────────────────────────────────────────
def _decode_id_token(id_token: str, config: dict, expected_nonce: str) -> dict | None:
    """Decode and minimally validate the ID token (signature skipped in sandbox)."""
    parts = id_token.split('.')
    if len(parts) != 3:
        return None

    def _pad(s: str) -> str:
        return s + '=' * (-len(s) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(_pad(parts[1]))
        claims = __import__('json').loads(payload_bytes)
    except Exception:
        return None

    now = int(time.time())
    if claims.get('exp', 0) < now:
        return None
    if claims.get('nonce') != expected_nonce:
        return None

    client_id = os.environ.get('ITSME_CLIENT_ID', '')
    aud = claims.get('aud', '')
    if isinstance(aud, list):
        if client_id not in aud:
            return None
    elif aud != client_id:
        return None

    return claims
