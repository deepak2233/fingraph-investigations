"""OIDC JWT validation for the private API. Public mode accepts fixed demo IDs."""

from dataclasses import dataclass
from functools import lru_cache
import os
from urllib.parse import urlparse

from fastapi import Header, HTTPException
import jwt


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    roles: frozenset[str]

    def require(self, role):
        if role not in self.roles:
            raise HTTPException(403, "Required role is missing")


@lru_cache
def jwks_client(url):
    if urlparse(url).scheme != "https":
        raise RuntimeError("OIDC_JWKS_URL must use HTTPS")
    return jwt.PyJWKClient(url, cache_keys=True, timeout=5)


def principal(authorization: str | None = Header(default=None)):
    issuer = os.getenv("OIDC_ISSUER")
    audience = os.getenv("OIDC_AUDIENCE")
    jwks_url = os.getenv("OIDC_JWKS_URL")
    if not all((issuer, audience, jwks_url)):
        raise HTTPException(503, "Private API requires configured OIDC authentication")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    try:
        key = jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub", "tenant_id"]},
        )
        tenant = claims["tenant_id"]
        subject = claims["sub"]
        roles = claims.get("roles", [])
        if not isinstance(tenant, str) or not tenant or not isinstance(subject, str) or not subject:
            raise ValueError("Invalid identity claims")
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise ValueError("Invalid roles claim")
        return Principal(subject, tenant, frozenset(roles))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "Invalid or expired token") from None
