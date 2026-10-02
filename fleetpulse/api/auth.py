"""AuthN/AuthZ: OIDC-style JWT validation, RBAC, tenant isolation.

Production: RS256 tokens from an OIDC provider (Keycloak / Entra / Cognito), keys fetched from JWKS.
Dev/test: HS256 with a shared secret (ENV=dev only). `aud`, `iss`, `exp` are always required.
"""
from __future__ import annotations
import os
import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request

ROLES = ("admin", "fleet_manager", "analyst", "viewer")
AUD, ISS = os.getenv("JWT_AUD", "fleetpulse-api"), os.getenv("JWT_ISS", "https://idp.fleetpulse.local")


@dataclass(frozen=True)
class Principal:
    sub: str
    tenant: str
    roles: frozenset[str]

    def has(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))

    @property
    def sees_exact_location(self) -> bool:
        return self.has("admin", "fleet_manager")


def _key_for(token: str):
    if os.getenv("JWKS_URL"):  # pragma: no cover - needs an IdP
        return jwt.PyJWKClient(os.environ["JWKS_URL"]).get_signing_key_from_jwt(token).key, ["RS256"]
    if os.getenv("ENV", "dev") != "dev":
        raise HTTPException(500, "auth misconfigured: JWKS_URL required outside dev")
    return os.getenv("JWT_SECRET", "dev-secret-change-me-32-bytes-minimum!!"), ["HS256"]


def decode(token: str) -> Principal:
    try:
        key, algs = _key_for(token)
        c = jwt.decode(token, key, algorithms=algs, audience=AUD, issuer=ISS,
                       options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except jwt.PyJWTError as e:
        raise HTTPException(401, "invalid token") from e
    roles = frozenset(r for r in c.get("roles", []) if r in ROLES)
    if "tenant" not in c or not roles:
        raise HTTPException(403, "token lacks tenant or roles")
    return Principal(c["sub"], c["tenant"], roles)


def issue_dev_token(sub: str, tenant: str, roles: list[str], ttl: int = 3600) -> str:
    now = int(time.time())
    key = os.getenv("JWT_SECRET", "dev-secret-change-me-32-bytes-minimum!!")
    return jwt.encode({"sub": sub, "tenant": tenant, "roles": roles, "aud": AUD, "iss": ISS,
                       "iat": now, "exp": now + ttl}, key, algorithm="HS256")


def current_principal(request: Request) -> Principal:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return decode(h[7:])


def require(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(*roles):
            raise HTTPException(403, "insufficient role")
        return p
    return dep
