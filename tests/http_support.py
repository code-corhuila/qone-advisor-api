"""Support for the HTTP and contract tests: an RSA pair per test session, tokens, the app over
the advisor over the seeded in-memory store."""

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from advisor.adapter.inbound.http.app import create_app
from advisor.adapter.inbound.http.auth import TokenVerifier
from advisor.application.usecase.advisor import AdvisorService
from tests.fakes import FakeSaga, MovableClock, SequenceIds, seeded_advisor


def _pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = (
    PRIVATE.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def mint(sub: str, role: str, name: str = "", minutes: int = 60, alg: str = "RS256", key=None, **extra) -> str:
    now = int(time.time())
    claims = {"iss": "qone-identity-api", "sub": sub, "role": role, "name": name, "iat": now, "exp": now + minutes * 60}
    claims.update(extra)
    if alg == "HS256":
        return jwt.encode(claims, "not-a-secret", algorithm="HS256")
    return jwt.encode(claims, _pem(key or PRIVATE), algorithm=alg)


def bearer(sub: str, role: str, name: str = "", **kw) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, role, name, **kw)}"}


def make_client(agent=None, saga=None):
    store, clock, saga = seeded_advisor(), MovableClock(), saga or FakeSaga()
    service = AdvisorService(store, store, agent, saga, clock, SequenceIds(), "2026-B")
    client = TestClient(create_app(service, TokenVerifier(PUBLIC_PEM)), raise_server_exceptions=False)
    return client, store, clock, saga
