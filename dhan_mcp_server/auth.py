"""External OAuth authorization server; validate signed access tokens locally."""
import asyncio
import os
from urllib.parse import urlsplit
import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

def https_url(name):
    value = os.environ[name]
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or url.query:
        raise ValueError(f"{name} must be a public HTTPS URL")
    return value

class JWTVerifier:
    def __init__(self, issuer, resource, jwks_url, subject, scope="dhan:read", jwks=None):
        self.issuer, self.resource, self.subject, self.scope = issuer, resource, subject, scope
        if not subject or not scope:
            raise ValueError("An authorized subject and scope are required")
        self.jwks = jwks or jwt.PyJWKClient(jwks_url, timeout=5, lifespan=300)

    async def verify_token(self, token: str):
        try:
            if len(token) > 16000:
                return None
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"],
                                audience=self.resource, issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            if claims["sub"] != self.subject:
                return None
            scopes = claims.get("scope", "")
            if not isinstance(scopes, str) or self.scope not in scopes.split():
                return None
            return AccessToken(token=token, client_id=str(claims.get("client_id", claims.get("azp", claims["sub"]))),
                               scopes=scopes.split(), expires_at=int(claims["exp"]))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError):
            return None

def remote_auth():
    issuer = https_url("MCP_OAUTH_ISSUER")
    resource = https_url("MCP_RESOURCE_URL")
    if urlsplit(resource).path != "/mcp":
        raise ValueError("MCP_RESOURCE_URL must end in /mcp")
    jwks = https_url("MCP_JWKS_URL")
    scope = os.getenv("MCP_REQUIRED_SCOPE", "dhan:read")
    verifier = JWTVerifier(issuer, resource, jwks, os.environ["MCP_ALLOWED_SUBJECT"], scope)
    return verifier, AuthSettings(issuer_url=AnyHttpUrl(issuer),
                                  resource_server_url=AnyHttpUrl(resource),
                                  required_scopes=[scope])
