import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from types import SimpleNamespace
from dhan_mcp_server.auth import JWTVerifier
from dhan_mcp_server.server import build_server

async def test_jwt_claims():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()))
    issuer = "https://issuer.example"
    resource = "https://mcp.example/mcp"
    verifier = JWTVerifier(issuer, resource, "", "owner", jwks=jwks)
    claims = dict(iss=issuer, aud=resource, sub="owner", scope="dhan:read",
                  iat=int(time.time()), exp=int(time.time())+60)
    def issue(**changes):
        return jwt.encode(claims | changes, private, algorithm="RS256")
    assert await verifier.verify_token(issue()) is not None
    for changes in ({"sub":"stranger"}, {"aud":"wrong"}, {"scope":"other"},
                    {"exp":0}, {"iss":"wrong"}):
        assert await verifier.verify_token(issue(**changes)) is None
    assert await verifier.verify_token("invalid") is None

async def test_tools_are_read_only():
    tools = await build_server().list_tools()
    assert len(tools) == 10
    assert all(t.annotations.readOnlyHint for t in tools)
    assert not any("order" in t.name for t in tools)
