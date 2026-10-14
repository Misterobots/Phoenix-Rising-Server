"""Bearer tokens: compact JWTs (HS256) signed with a per-install secret. The client treats them as opaque."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


class Tokens:
    def __init__(self, secret_path):
        if not os.path.exists(secret_path):
            os.makedirs(os.path.dirname(os.path.abspath(secret_path)), exist_ok=True)
            with open(secret_path, 'w') as f:
                f.write(secrets.token_hex(32))
        self.key = open(secret_path).read().strip().encode()

    def mint(self, kind, ttl, **claims):
        now = int(time.time())
        body = dict(claims, t=kind, iat=now, exp=now + ttl, jti=secrets.token_hex(8))
        head = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
        payload = _b64(json.dumps(body, separators=(',', ':')).encode())
        sig = _b64(hmac.new(self.key, (head + '.' + payload).encode(), hashlib.sha256).digest())
        return head + '.' + payload + '.' + sig

    def read(self, token):
        """-> claims dict for a valid, unexpired token we minted, else None."""
        try:
            head, payload, sig = token.split('.')
            want = _b64(hmac.new(self.key, (head + '.' + payload).encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(want, sig):
                return None
            claims = json.loads(_unb64(payload))
        except (ValueError, json.JSONDecodeError):
            return None
        return claims if claims.get('exp', 0) >= time.time() else None

    def from_request(self, request):
        auth = request.headers.get('Authorization', '')
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            return self.read(parts[1].strip())
        return None
