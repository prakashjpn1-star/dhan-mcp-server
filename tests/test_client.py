import httpx
import pytest
from dhan_mcp_server.client import DhanClient, BrokerError

@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "mock-token")
    monkeypatch.setenv("DHAN_CLIENT_ID", "mock-client")

async def no_sleep(_):
    pass

async def test_read_only_cache_and_headers():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["access-token"] == "mock-token"
        assert req.headers["client-id"] == "mock-client"
        return httpx.Response(200, json={"data": 42})
    client = DhanClient(httpx.MockTransport(handler), sleep=no_sleep)
    payload = {"NSE_EQ": [123]}
    assert await client.request("marketfeed/ltp", payload) == {"data": 42}
    await client.request("marketfeed/ltp", payload)
    assert len(calls) == 1
    assert calls[0].url.path == "/v2/marketfeed/ltp"
    await client.request("holdings")
    await client.request("holdings")
    assert len(calls) == 3
    with pytest.raises(BrokerError):
        await client.request("orders", {})
    assert len(calls) == 3

async def test_retry_and_expired_token():
    statuses = iter([429, 503, 200, 401])
    def handler(req):
        return httpx.Response(next(statuses), json={"data": 1})
    client = DhanClient(httpx.MockTransport(handler), sleep=no_sleep)
    assert await client.request("positions") == {"data": 1}
    with pytest.raises(BrokerError, match="credentials"):
        await client.request("positions")

async def test_search():
    def handler(req):
        assert "access-token" not in req.headers
        return httpx.Response(200, text="SEM_TRADING_SYMBOL,SEM_CUSTOM_SYMBOL,SEM_SMST_SECURITY_ID,SEM_EXM_EXCH_ID,SEM_SEGMENT\nUGROCAP,UGRO CAPITAL,123,NSE,E\n")
    client = DhanClient(httpx.MockTransport(handler), sleep=no_sleep)
    assert (await client.search("ugro"))[0]["security_id"] == "123"
