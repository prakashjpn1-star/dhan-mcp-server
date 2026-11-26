"""Read-only MCP entry point for local STDIO and OAuth-protected HTTP."""
import argparse
import os
from datetime import date, datetime
from typing import Literal
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from .client import DhanClient, credential
from .auth import remote_auth
from urllib.parse import urlsplit

def build_server(remote=False, client=None):
    broker = client or DhanClient()
    options = {}
    if remote:
        verifier, auth = remote_auth()
        host = urlsplit(os.environ["MCP_RESOURCE_URL"]).netloc
        options = dict(token_verifier=verifier, auth=auth,
                       host=os.getenv("MCP_BIND_HOST", "127.0.0.1"),
                       port=int(os.getenv("MCP_PORT", "8000")),
                       stateless_http=True, json_response=True,
                       transport_security=TransportSecuritySettings(
                           enable_dns_rebinding_protection=True,
                           allowed_hosts=[host],
                           allowed_origins=["https://" + host, "https://chatgpt.com"]))
    mcp = FastMCP("Dhan read-only", **options)
    annotation = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @mcp.tool(annotations=annotation)
    async def get_holdings():
        """Return the configured Dhan account's long-term holdings."""
        return await broker.request("holdings")

    @mcp.tool(annotations=annotation)
    async def get_positions():
        """Return open positions without modifying them."""
        return await broker.request("positions")

    @mcp.tool(annotations=annotation)
    async def get_fund_limits():
        """Return account funds and margin limits."""
        return await broker.request("fundlimit")

    @mcp.tool(annotations=annotation)
    async def validate_token():
        """Check the broker token's current validity without revealing it."""
        data = await broker.request("profile")
        return {key: data.get(key) for key in ("tokenValidity", "activeSegment", "dataPlan", "dataValidity")}

    @mcp.tool(annotations=annotation)
    async def search_instruments(query: str, limit: int = 20):
        """Find Dhan instrument security IDs using ticker or company name."""
        return await broker.search(query, limit)

    def validate_instruments(instruments):
        allowed = {"NSE_EQ", "BSE_EQ", "NSE_FNO", "BSE_FNO", "NSE_CURRENCY", "BSE_CURRENCY", "MCX_COMM", "IDX_I"}
        if not instruments or not set(instruments) <= allowed or not 1 <= sum(map(len, instruments.values())) <= 1000:
            raise ValueError("Provide 1–1000 instrument IDs grouped by valid Dhan exchange segment")
        if any(i <= 0 for ids in instruments.values() for i in ids):
            raise ValueError("Security IDs must be positive")
        return instruments

    @mcp.tool(annotations=annotation)
    async def get_market_ltp(instruments: dict[str, list[int]]):
        """Get live last traded prices; search instruments first if ID is unknown."""
        return await broker.request("marketfeed/ltp", validate_instruments(instruments))

    @mcp.tool(annotations=annotation)
    async def get_market_ohlc(instruments: dict[str, list[int]]):
        """Get current OHLC and latest price for instruments."""
        return await broker.request("marketfeed/ohlc", validate_instruments(instruments))

    @mcp.tool(annotations=annotation)
    async def get_market_depth(instruments: dict[str, list[int]]):
        """Get quote snapshot, including available market depth."""
        return await broker.request("marketfeed/quote", validate_instruments(instruments))

    @mcp.tool(annotations=annotation)
    async def get_historical_data(security_id: str, exchange_segment: str, instrument: str,
                                  from_date: str, to_date: str, expiry_code: Literal[0, 1, 2] = 0):
        """Daily historical candles; dates YYYY-MM-DD."""
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if start >= end or not security_id.isdigit():
            raise ValueError("Invalid date range or security ID")
        return await broker.request("charts/historical", dict(securityId=security_id,
            exchangeSegment=exchange_segment, instrument=instrument, expiryCode=expiry_code,
            fromDate=from_date, toDate=to_date, oi=False))

    @mcp.tool(annotations=annotation)
    async def get_intraday_data(security_id: str, exchange_segment: str, instrument: str,
                                from_date: str, to_date: str, interval: Literal[1, 5, 15, 25, 60] = 5):
        """Intraday candles; use IST timestamps YYYY-MM-DD HH:MM:SS; up to 90 days."""
        start, end = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S"), datetime.strptime(to_date, "%Y-%m-%d %H:%M:%S")
        if start >= end or (end-start).total_seconds() > 90*86400 or not security_id.isdigit():
            raise ValueError("Invalid date range or security ID")
        return await broker.request("charts/intraday", dict(securityId=security_id,
            exchangeSegment=exchange_segment, instrument=instrument, interval=str(interval),
            fromDate=from_date, toDate=to_date, oi=False))
    return mcp

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Read-only Dhan MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    args = parser.parse_args()
    credential("DHAN_ACCESS_TOKEN")
    credential("DHAN_CLIENT_ID")
    build_server(remote=args.transport == "streamable-http").run(transport=args.transport)

if __name__ == "__main__":
    main()
