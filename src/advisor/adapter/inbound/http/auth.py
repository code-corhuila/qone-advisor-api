"""Token verification (norm 5.3.7): every service validates the JWT by itself with the identity
public key. `alg` must be exactly RS256; `exp` and `sub` are required; 30 s of skew. A SERVICE
token carries the service name as subject and no user id."""

import base64
import json
from uuid import UUID

import jwt

from advisor.application.port.inbound.caller import Caller, Role

ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 30


class AuthError(Exception):
    def __init__(self, message: str = "missing or invalid token") -> None:
        super().__init__(message)
        self.message = message


class TokenVerifier:
    def __init__(self, public_key_pem: str) -> None:
        self._key = public_key_pem

    def verify(self, authorization: str | None) -> Caller:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError()
        token = authorization[len("Bearer ") :].strip()
        if _declared_alg(token) != "RS256":
            raise AuthError()
        try:
            claims = jwt.decode(
                token, self._key, algorithms=ALGORITHMS, leeway=LEEWAY_SECONDS, options={"require": ["exp", "sub"]}
            )
            role = Role(claims.get("role", ""))
            subject = str(claims["sub"])
            user_id = None if role is Role.SERVICE else UUID(subject)
            return Caller(user_id, role, str(claims.get("name", "")), subject)
        except (jwt.PyJWTError, ValueError) as cause:
            raise AuthError() from cause


def _declared_alg(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    return header.get("alg") if isinstance(header, dict) else None
