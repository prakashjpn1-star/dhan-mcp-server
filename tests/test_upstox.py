import httpx
import pytest
from dhan_mcp_server.upstox import UpstoxMarket
from dhan_mcp_server.client import BrokerError

async def test_quotes_and_cache(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "synthetic-token")
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == "GET"
        assert req.url.host == "api.upstox.com"
        assert req.headers["authorization"] == "Bearer synthetic-token"
        assert req.url.params["instrument_key"] == "NSE_EQ|INE002A01018"
        return httpx.Response(200,json={"status":"success","data":{"quote":{"last_price":100}}})
    api = UpstoxMarket(httpx.MockTransport(handler))
    result = await api.quotes(["NSE_EQ|INE002A01018"])
    assert result["source"] == "Upstox"
    assert result["data"]["quote"]["last_price"] == 100
    assert await api.quotes(["NSE_EQ|INE002A01018"]) == result
    assert len(calls) == 1
    with pytest.raises(ValueError):
        await api.quotes(["../../orders"])

async def test_denied(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "synthetic-token")
    api = UpstoxMarket(httpx.MockTransport(lambda _: httpx.Response(403)))
    with pytest.raises(BrokerError,match="403"):
        await api.search("UGROCAP")
