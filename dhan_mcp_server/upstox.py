"""Read-only Upstox market data; no trading or portfolio endpoints."""
import asyncio
import hashlib
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from urllib.parse import quote
import httpx
from .client import credential, BrokerError

class UpstoxMarket:
    def __init__(self, transport=None):
        self.transport = transport
        self.lock = asyncio.Lock()
        self.cache = OrderedDict()
        self.last = 0
        self.identity = None

    async def _get(self, path, params=None, ttl=2):
        token = credential("UPSTOX_ANALYTICS_TOKEN")
        identity = hashlib.sha256(token.encode()).digest()
        key = (path, tuple(sorted((params or {}).items())))
        async with self.lock:
            if self.identity != identity:
                self.cache.clear()
                self.identity = identity
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            async with httpx.AsyncClient(base_url="https://api.upstox.com/", timeout=20,
                    follow_redirects=False, trust_env=False, transport=self.transport,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"}) as http:
                for attempt in range(3):
                    await asyncio.sleep(max(0, self.last + 0.25 - time.monotonic()))
                    self.last = time.monotonic()
                    try:
                        response = await http.get(path, params=params)
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt == 2:
                            raise BrokerError("Upstox network unavailable") from None
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        try:
                            delay = max(2 ** attempt, float(response.headers.get("Retry-After", "0")))
                        except ValueError:
                            delay = 2 ** attempt
                        if delay > 30:
                            raise BrokerError("Upstox rate limited; retry later")
                        await asyncio.sleep(delay)
                        continue
                    if response.status_code != 200:
                        raise BrokerError(f"Upstox request rejected (HTTP {response.status_code})")
                    try:
                        data = response.json()
                    except ValueError:
                        raise BrokerError("Invalid Upstox response") from None
                    if not isinstance(data, dict) or data.get("status") != "success":
                        raise BrokerError("Upstox API returned an error")
                    result = {"source": "Upstox", "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                              "data": data.get("data")}
                    self.cache[key] = (time.monotonic()+ttl, result)
                    self.cache.move_to_end(key)
                    while len(self.cache) > 128:
                        self.cache.popitem(last=False)
                    return result
        raise BrokerError("Upstox request failed")

    @staticmethod
    def key(value):
        if not re.fullmatch(r"(NSE_EQ|BSE_EQ)\|[A-Z0-9]{12}", value):
            raise ValueError("Use an NSE_EQ or BSE_EQ instrument key returned by search")
        return value

    async def search(self, query, exchange="NSE"):
        if not 1 <= len(query.strip()) <= 50 or exchange not in ("NSE", "BSE"):
            raise ValueError("Invalid search query or exchange")
        return await self._get("v2/instruments/search", {"query":query.strip(),
             "exchanges":exchange,"segments":"EQ","page_number":"1","records":"30"}, 300)

    async def quotes(self, keys):
        if not 1 <= len(keys) <= 50:
            raise ValueError("Provide 1–50 instrument keys")
        return await self._get("v2/market-quote/quotes",
                               {"instrument_key":",".join(self.key(k) for k in keys)})

    async def history(self, key, start, end, interval=1, unit="days"):
        start, end = date.fromisoformat(start), date.fromisoformat(end)
        if start > end or (end-start).days > (30 if unit=="minutes" else 365):
            raise ValueError("Date range exceeds supported limit")
        if unit not in ("days","minutes") or (unit=="days" and interval!=1) or (unit=="minutes" and not 1<=interval<=300):
            raise ValueError("Invalid candle interval")
        path = f"v3/historical-candle/{quote(self.key(key), safe='')}/{unit}/{interval}/{end}/{start}"
        return await self._get(path, ttl=300)

    async def intraday(self, key, interval=5):
        if not 1<=interval<=300:
            raise ValueError("Invalid interval")
        return await self._get(f"v3/historical-candle/intraday/{quote(self.key(key),safe='')}/minutes/{interval}", ttl=10)
