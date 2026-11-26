"""Render ASGI entry point. Missing configuration never exposes broker tools."""
import os
import asyncio
import logging
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from .client import credential, BrokerError

async def broker_smoke_test():
    """One startup check, private logs only. No account data or secrets in logs."""
    from .client import DhanClient
    from .server import build_server
    log = logging.getLogger("uvicorn.error")
    broker = DhanClient()
    server = build_server(client=broker)
    try:
        tools = await server.list_tools()
        log.info("DHAN_SMOKE MCP tool registration PASS (%d tools)", len(tools))
    except Exception:
        log.warning("DHAN_SMOKE MCP tool registration FAIL")
        return
    if os.getenv("UPSTOX_ANALYTICS_TOKEN") or os.getenv("UPSTOX_ANALYTICS_TOKEN_FILE"):
        try:
            from .upstox import UpstoxMarket
            market = UpstoxMarket()
            search = await market.search("UGROCAP")
            match = next(row for row in search["data"] if row.get("trading_symbol") == "UGROCAP" and row.get("segment") == "NSE_EQ")
            result = await market.quotes([match["instrument_key"]])
            row = next(row for row in result["data"].values() if row.get("instrument_token") == match["instrument_key"])
            price = row.get("last_price")
            if isinstance(price, (int, float)) and not isinstance(price, bool) and 0 < price < 1e9:
                log.info("DHAN_SMOKE UPSTOX UGROCAP LTP PASS: INR %.2f", price)
            else:
                log.warning("DHAN_SMOKE UPSTOX quote missing")
        except BrokerError as exc:
            log.warning("DHAN_SMOKE UPSTOX FAIL: %s", str(exc))
        except Exception:
            log.warning("DHAN_SMOKE UPSTOX FAIL: unexpected error")
    for label, path in (("profile", "profile"), ("holdings", "holdings"), ("positions", "positions")):
        try:
            await broker.request(path)
            log.info("DHAN_SMOKE %s PASS", label)
        except BrokerError as exc:
            log.warning("DHAN_SMOKE %s FAIL: %s", label, str(exc))
        except Exception:
            log.warning("DHAN_SMOKE %s FAIL: unexpected error", label)
    try:
        rows = await broker.search("UGROCAP", 50)
        match = next((r for r in rows if r["symbol"].upper() == "UGROCAP" and r["exchange"] == "NSE" and r["segment"] == "E"), None)
        if not match:
            log.warning("DHAN_SMOKE instrument search FAIL: NSE equity not found")
            return
        log.info("DHAN_SMOKE instrument search PASS")
        data = await broker.request("marketfeed/ltp", {"NSE_EQ": [int(match["security_id"])]})
        quote = data.get("data", {}).get("NSE_EQ", {}).get(match["security_id"], {})
        price = quote.get("last_price")
        if isinstance(price, (int, float)) and not isinstance(price, bool) and 0 < price < 1e9:
            log.info("DHAN_SMOKE UGROCAP LTP PASS: INR %.2f", price)
        else:
            log.warning("DHAN_SMOKE UGROCAP LTP FAIL: quote missing")
    except BrokerError as exc:
        log.warning("DHAN_SMOKE market data FAIL: %s", str(exc))
    except Exception:
        log.warning("DHAN_SMOKE market data FAIL: unexpected error")

@asynccontextmanager
async def pending_lifespan(app):
    task = None
    if os.getenv("DHAN_STARTUP_SMOKE_TEST", "").lower() == "true":
        task = asyncio.create_task(broker_smoke_test())
    try:
        yield
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
        return Starlette(lifespan=pending_lifespan, routes=[Route("/", health), Route("/health", health),
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
