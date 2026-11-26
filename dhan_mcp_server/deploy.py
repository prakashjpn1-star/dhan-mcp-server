"""Render ASGI entry point. Missing configuration never exposes broker tools."""
import os
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from .client import credential, BrokerError

def create_app():
    load_dotenv()
    required = ("MCP_OAUTH_ISSUER", "MCP_JWKS_URL", "MCP_RESOURCE_URL", "MCP_ALLOWED_SUBJECT")
    try:
        credential("DHAN_ACCESS_TOKEN")
        credential("DHAN_CLIENT_ID")
        configured = all(os.getenv(key, "").strip() for key in required)
    except (BrokerError, OSError, UnicodeError):
        configured = False
    if not configured:
        async def health(request):
            return JSONResponse({"status": "configuration_required", "mcp_enabled": False})
        async def blocked(request):
            return JSONResponse({"error": "MCP unavailable: configuration required"}, status_code=503)
        return Starlette(routes=[Route("/", health), Route("/health", health),
                                 Route("/mcp", blocked, methods=["GET", "POST", "DELETE"])])
    from .server import build_server
    mcp = build_server(remote=True)
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ready", "mcp_enabled": True})
    return mcp.streamable_http_app()

def main():
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", os.getenv("MCP_PORT", "8000"))))

if __name__ == "__main__":
    main()
