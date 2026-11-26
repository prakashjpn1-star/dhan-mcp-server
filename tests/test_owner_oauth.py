import base64
import hashlib
from urllib.parse import parse_qs,urlsplit
import httpx
from starlette.applications import Starlette
from dhan_mcp_server.owner_oauth import OwnerOAuth,CALLBACK,SCOPE
from dhan_mcp_server.deploy import create_app

RESOURCE="https://test.example/mcp"
ORIGIN="https://test.example"
VERIFIER="a"*43
CHALLENGE=base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")

async def test_owner_flow():
    owner=OwnerOAuth(RESOURCE,"test-password-not-real-"*3)
    app=Starlette(routes=owner.routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
        assert (await client.get("/.well-known/oauth-authorization-server")).json()["code_challenge_methods_supported"]==["S256"]
        assert (await client.post("/register",json={"redirect_uris":["https://evil.example"]})).status_code==400
        reg=await client.post("/register",json={"redirect_uris":[CALLBACK],"token_endpoint_auth_method":"none"})
        assert reg.status_code==201
        cid=reg.json()["client_id"]
        params=dict(client_id=cid,redirect_uri=CALLBACK,response_type="code",code_challenge=CHALLENGE,
                    code_challenge_method="S256",resource=RESOURCE,state="state",scope=SCOPE)
        assert (await client.get("/authorize",params={**params,"code_challenge_method":"plain"})).status_code==400
        assert (await client.get("/authorize",params={**params,"resource":"https://evil.example"})).status_code==400
        response=await client.get("/authorize",params=params)
        assert response.status_code==200
        nonce=client.cookies["__Host-dhan-oauth"]
        assert (await client.post("/authorize",data={"nonce":nonce,"password":"wrong","decision":"allow"},headers={"origin":ORIGIN})).status_code==403
        await client.get("/authorize",params=params)
        nonce=client.cookies["__Host-dhan-oauth"]
        payload={"nonce":nonce,"password":"test-password-not-real-"*3,"decision":"allow"}
        assert (await client.post("/authorize",data=payload,headers={"origin":"https://evil.example"})).status_code==400
        response=await client.post("/authorize",data=payload,headers={"origin":ORIGIN})
        assert response.status_code==303
        q=parse_qs(urlsplit(response.headers["location"]).query)
        assert q["iss"]==[ORIGIN] and q["state"]==["state"]
        payload=dict(grant_type="authorization_code",client_id=cid,redirect_uri=CALLBACK,
                     code=q["code"][0],code_verifier=VERIFIER,resource=RESOURCE)
        assert (await client.post("/token",data={**payload,"code_verifier":"b"*43})).status_code==400
        assert (await client.post("/token",data={**payload,"resource":"https://evil.example"})).status_code==400
        response=await client.post("/token",data=payload)
        assert response.status_code==200
        token=response.json()
        assert await owner.verify_token(token["access_token"])
        assert await owner.verify_token("forged") is None
        assert (await client.post("/token",data=payload)).status_code==400
        refresh=dict(grant_type="refresh_token",client_id=cid,refresh_token=token["refresh_token"],resource=RESOURCE)
        rotated=await client.post("/token",data=refresh)
        assert rotated.status_code==200
        assert await owner.verify_token(token["access_token"]) is None
        assert (await client.post("/token",data=refresh)).status_code==400
        assert await owner.verify_token(rotated.json()["access_token"]) is None

async def test_protected_mcp(monkeypatch):
    for k,v in {"MCP_AUTH_MODE":"self-hosted","MCP_RESOURCE_URL":RESOURCE,
                "MCP_OWNER_PASSWORD":"synthetic-password-"*3,"DHAN_ACCESS_TOKEN":"test",
                "DHAN_CLIENT_ID":"test"}.items():
        monkeypatch.setenv(k,v)
    app=create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            r=await client.get("/.well-known/oauth-protected-resource/mcp")
            assert r.status_code==200
            assert r.json()["resource"]==RESOURCE
            assert (await client.get("/.well-known/oauth-authorization-server")).status_code==200
            r=await client.post("/mcp",json={"jsonrpc":"2.0","id":1,"method":"tools/list"})
            assert r.status_code==401
            assert "resource_metadata" in r.headers["www-authenticate"]
            assert (await client.post("/mcp",headers={"Authorization":"Bearer fake"})).status_code==401

async def test_expiry():
    owner=OwnerOAuth(RESOURCE,"synthetic-password-"*3)
    from dhan_mcp_server.owner_oauth import digest
    owner.access[digest("expired")]={"client":"x","expires":0,"family":"f"}
    assert await owner.verify_token("expired") is None
