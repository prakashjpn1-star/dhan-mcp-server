"""Single-owner OAuth server for a single-worker ephemeral Render instance.

Opaque tokens and all transient state live in bounded memory; restart revokes
sessions. No user-controlled outbound requests or redirect destinations.
"""
import base64
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from urllib.parse import urlencode, parse_qs
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.routing import Route
from .auth import https_url
from .client import credential

CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
SCOPE = "dhan:read"
HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache",
           "Referrer-Policy": "same-origin", "X-Content-Type-Options": "nosniff",
           "X-Frame-Options": "DENY",
           "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}
ACCESS_TTL = 900
REFRESH_TTL = 7 * 86400

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def random_id():
    return secrets.token_urlsafe(32)

def json_response(data, status=200):
    return JSONResponse(data, status_code=status, headers=HEADERS)

def error(code, status=400):
    return json_response({"error": code}, status)

async def body(request, kind):
    if request.headers.get("content-type", "").split(";")[0].strip() != kind:
        raise ValueError("content type")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > 16384:
            raise ValueError("body too large")
    if kind == "application/json":
        import json
        data = json.loads(buf)
        if not isinstance(data, dict):
            raise ValueError("invalid JSON")
        return data
    fields = parse_qs(buf.decode(), keep_blank_values=True, max_num_fields=32)
    if any(len(v) != 1 for v in fields.values()):
        raise ValueError("duplicate fields")
    return {k: v[0] for k, v in fields.items()}

class OwnerOAuth:
    def __init__(self, resource, password):
        from urllib.parse import urlsplit
        u = urlsplit(resource)
        if u.scheme != "https" or u.path != "/mcp" or not u.hostname or u.query or u.fragment or u.username or u.password:
            raise ValueError("Invalid MCP resource")
        if len(password) < 32:
            raise ValueError("Owner password requires at least 32 characters")
        self.resource = resource
        self.issuer = resource.removesuffix("/mcp")
        self.password_hash = digest(password)
        self.clients = {}
        self.pending = {}
        self.codes = {}
        self.access = {}
        self.refresh = {}
        self.failures = []
        self.client_key = secrets.token_bytes(32)

    def settings(self):
        return AuthSettings(issuer_url=AnyHttpUrl(self.issuer),
                            resource_server_url=AnyHttpUrl(self.resource),
                            required_scopes=[SCOPE])

    def metadata(self):
        return {"issuer": self.issuer, "authorization_endpoint": self.issuer+"/authorize",
                "token_endpoint": self.issuer+"/token", "registration_endpoint": self.issuer+"/register",
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code","refresh_token"],
                "token_endpoint_auth_methods_supported": ["none","client_secret_post","client_secret_basic"],
                "code_challenge_methods_supported": ["S256"], "scopes_supported": [SCOPE],
                "authorization_response_iss_parameter_supported": True}

    def prune(self):
        now = time.time()
        for store in (self.pending, self.codes, self.access, self.refresh):
            for key in list(store):
                if store[key]["expires"] <= now:
                    del store[key]
        self.failures = [t for t in self.failures if t > now-300]

    def client(self, cid):
        # Stable public client ID; confidential secrets are derived from an
        # ephemeral key and are invalidated by a restart along with tokens.
        if not isinstance(cid, str) or not re.fullmatch(r"dhan-(none|post|basic)-[A-Za-z0-9_-]{43}", cid):
            return None
        mode = cid.split("-")[1]
        return mode

    def client_secret(self, cid):
        return hmac.new(self.client_key, cid.encode(), hashlib.sha256).hexdigest()

    async def register(self, request):
        try:
            d = await body(request, "application/json")
            if d.get("redirect_uris") != [CALLBACK]:
                return error("invalid_redirect_uri")
            if d.get("response_types", ["code"]) != ["code"]:
                return error("invalid_client_metadata")
            grants = d.get("grant_types", ["authorization_code","refresh_token"])
            if not isinstance(grants,list) or "authorization_code" not in grants or not set(grants) <= {"authorization_code","refresh_token"}:
                return error("invalid_client_metadata")
            method = d.get("token_endpoint_auth_method","none")
            modes = {"none":"none","client_secret_post":"post","client_secret_basic":"basic"}
            if method not in modes:
                return error("invalid_client_metadata")
            cid = "dhan-"+modes[method]+"-"+random_id()
            result = {"client_id":cid,"redirect_uris":[CALLBACK],"response_types":["code"],
                      "grant_types":["authorization_code","refresh_token"],
                      "token_endpoint_auth_method":method,"scope":SCOPE}
            if method != "none":
                result.update(client_secret=self.client_secret(cid),client_secret_expires_at=0)
            return json_response(result,201)
        except (ValueError,TypeError,UnicodeError):
            return error("invalid_client_metadata")

    async def authorize(self, request):
        self.prune()
        if request.method == "GET":
            if len(request.url.query)>8192:
                return error("invalid_request")
            p = request.query_params
            if any(len(p.getlist(k))!=1 for k in p.keys()):
                return error("invalid_request")
            if not self.client(p.get("client_id")) or p.get("redirect_uri")!=CALLBACK:
                return error("invalid_request")
            if (p.get("response_type")!="code" or p.get("code_challenge_method")!="S256"
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}",p.get("code_challenge",""))
                or p.get("resource")!=self.resource or p.get("scope",SCOPE)!=SCOPE
                or not 1<=len(p.get("state",""))<=2048):
                return error("invalid_request")
            if len(self.pending)>=256:
                return error("temporarily_unavailable",503)
            nonce = random_id()
            self.pending[digest(nonce)] = dict(client=p["client_id"],challenge=p["code_challenge"],
                state=p["state"],expires=time.time()+300)
            page = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Dhan portfolio authorization</title>
            <h1>Authorize read-only Dhan portfolio access</h1><p>Allow ChatGPT to read holdings, positions and funds. No order placement.</p>
            <form method="post" action="/authorize"><input type="hidden" name="nonce" value="%s">
            <label>Owner password <input type="password" name="password" required autocomplete="current-password" maxlength="512"></label>
            <button type="submit" name="decision" value="allow">Authorize</button>
            <button type="submit" name="decision" value="deny" formnovalidate>Deny</button></form></html>""" % html.escape(nonce)
            resp=HTMLResponse(page,headers=HEADERS)
            resp.set_cookie("__Host-dhan-oauth",nonce,max_age=300,secure=True,httponly=True,samesite="lax",path="/")
            return resp
        try:
            p=await body(request,"application/x-www-form-urlencoded")
        except (ValueError,UnicodeError):
            logging.getLogger(__name__).warning("OAUTH_FORM rejected form encoding")
            return error("invalid_request")
        # Browser privacy policies can serialize an otherwise same-origin
        # form POST's Origin as null. Require positive Fetch Metadata evidence
        # in that case, in addition to the one-time nonce and host-only cookie.
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site in ("cross-site", "same-site") or (
            origin != self.issuer and not (
                origin in (None, "null") and fetch_site == "same-origin"
            )
        ):
            logging.getLogger(__name__).warning("OAUTH_FORM rejected origin policy")
            return error("invalid_request")
        nonce=p.get("nonce","")
        cookie=request.cookies.get("__Host-dhan-oauth","")
        if not nonce or not hmac.compare_digest(nonce.encode(),cookie.encode()):
            logging.getLogger(__name__).warning("OAUTH_FORM rejected nonce/cookie binding")
            return error("invalid_request")
        pending=self.pending.pop(digest(nonce),None)
        if not pending or pending["expires"]<=time.time():
            logging.getLogger(__name__).warning("OAUTH_FORM rejected expired or consumed transaction")
            return error("invalid_request")
        if p.get("decision")=="deny":
            return self.redirect(pending,error="access_denied")
        if len(self.failures)>=5:
            return error("temporarily_unavailable",429)
        if p.get("decision")!="allow" or not hmac.compare_digest(digest(p.get("password","")),self.password_hash):
            self.failures.append(time.time())
            return HTMLResponse("Authorization failed. Restart authorization to try again.",status_code=403,headers=HEADERS)
        if len(self.codes)>=256:
            return error("temporarily_unavailable",503)
        code=random_id()
        self.codes[digest(code)]={**pending,"expires":time.time()+90}
        return self.redirect(pending,code=code)

    def redirect(self,pending,**fields):
        resp=RedirectResponse(CALLBACK+"?"+urlencode({**fields,"state":pending["state"],"iss":self.issuer}),
                              status_code=303,headers=HEADERS)
        resp.delete_cookie("__Host-dhan-oauth",path="/",secure=True,httponly=True,samesite="lax")
        return resp

    def authenticate_client(self,request,p):
        cid=p.get("client_id","")
        auth=request.headers.get("authorization","")
        if auth:
            if not auth.startswith("Basic ") or p.get("client_secret"):
                return None
            try:
                decoded=base64.b64decode(auth[6:],validate=True).decode()
                basic_id,secret=decoded.split(":",1)
            except (ValueError,UnicodeError):
                return None
            if cid and cid!=basic_id:
                return None
            cid=basic_id
            if self.client(cid)!="basic" or not hmac.compare_digest(secret.encode(),self.client_secret(cid).encode()):
                return None
        else:
            mode=self.client(cid)
            if mode=="post":
                if not hmac.compare_digest(p.get("client_secret","").encode(),self.client_secret(cid).encode()):
                    return None
            elif mode!="none" or p.get("client_secret"):
                return None
        return cid

    def revoke_family(self,family):
        for store in (self.access,self.refresh):
            for k in list(store):
                if store[k]["family"]==family:
                    del store[k]

    def issue(self,cid,family,deadline):
        if len(self.access)>=512 or len(self.refresh)>=2048:
            return error("temporarily_unavailable",503)
        access,refresh=random_id(),random_id()
        now=time.time()
        expiry=min(now+ACCESS_TTL,deadline)
        self.access[digest(access)]={"client":cid,"expires":expiry,"family":family}
        self.refresh[digest(refresh)]={"client":cid,"expires":deadline,"family":family,"used":False}
        return json_response({"access_token":access,"token_type":"Bearer","expires_in":int(expiry-now),
                              "refresh_token":refresh,"scope":SCOPE})

    async def token(self,request):
        self.prune()
        try:
            p=await body(request,"application/x-www-form-urlencoded")
        except (ValueError,UnicodeError):
            return error("invalid_request")
        cid=self.authenticate_client(request,p)
        if not cid:
            return error("invalid_client",401)
        if p.get("resource")!=self.resource or p.get("scope",SCOPE)!=SCOPE:
            return error("invalid_target")
        if p.get("grant_type")=="authorization_code":
            key=digest(p.get("code",""))
            record=self.codes.get(key)
            verifier=p.get("code_verifier","")
            if (not record or record["client"]!=cid or p.get("redirect_uri")!=CALLBACK
                or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}",verifier)):
                return error("invalid_grant")
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if not hmac.compare_digest(challenge,record["challenge"]):
                return error("invalid_grant")
            del self.codes[key]
            return self.issue(cid,random_id(),time.time()+REFRESH_TTL)
        if p.get("grant_type")=="refresh_token":
            record=self.refresh.get(digest(p.get("refresh_token","")))
            if not record or record["client"]!=cid:
                return error("invalid_grant")
            if record["used"]:
                self.revoke_family(record["family"])
                return error("invalid_grant")
            record["used"]=True
            for k in list(self.access):
                if self.access[k]["family"]==record["family"]:
                    del self.access[k]
            return self.issue(cid,record["family"],record["expires"])
        return error("unsupported_grant_type")

    async def verify_token(self,token):
        if len(token)>256:
            return None
        self.prune()
        row=self.access.get(digest(token))
        if not row:
            return None
        return AccessToken(token=token,client_id=row["client"],scopes=[SCOPE],expires_at=int(row["expires"]))

    def routes(self):
        async def metadata(request):
            return json_response(self.metadata())
        return [Route("/.well-known/oauth-authorization-server",metadata),
                Route("/register",self.register,methods=["POST"]),
                Route("/authorize",self.authorize,methods=["GET","POST"]),
                Route("/token",self.token,methods=["POST"])]

def owner_auth():
    return OwnerOAuth(https_url("MCP_RESOURCE_URL"),credential("MCP_OWNER_PASSWORD"))
