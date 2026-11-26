"""Read-only Dhan v2 client. All outbound broker paths are allowlisted."""
import asyncio
import csv
import io
import json
import os
import time
from collections import OrderedDict
from pathlib import Path
import httpx

BASE = "https://api.dhan.co/v2/"
MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
READS = {
    "profile": "GET", "holdings": "GET", "positions": "GET", "fundlimit": "GET",
    "marketfeed/ltp": "POST", "marketfeed/ohlc": "POST", "marketfeed/quote": "POST",
    "charts/historical": "POST", "charts/intraday": "POST",
}
class BrokerError(RuntimeError):
    pass

def credential(name: str) -> str:
    path = os.getenv(name + "_FILE")
    value = Path(path).read_text(encoding="utf-8").strip() if path else os.getenv(name, "").strip()
    if not value:
        raise BrokerError(f"Missing {name} or {name}_FILE")
    return value

class DhanClient:
    def __init__(self, transport=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.transport = transport
        self.clock, self.sleep = clock, sleep
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.last = {}
        self.identity = None
        self.master = None

    async def request(self, path: str, payload=None):
        if path not in READS:
            raise BrokerError("Endpoint not permitted")
        token, client_id = credential("DHAN_ACCESS_TOKEN"), credential("DHAN_CLIENT_ID")
        # Never cache portfolio or profile data; reset public cache on credential rotation.
        async with self.lock:
            import hashlib
            identity = hashlib.sha256((client_id + "\0" + token).encode()).digest()
            if self.identity != identity:
                self.cache.clear()
                self.identity = identity
            ttl = 2 if path.startswith("marketfeed/") else 300 if path.startswith("charts/") else 0
            key = (path, json.dumps(payload, sort_keys=True))
            if ttl and key in self.cache:
                expires, data = self.cache[key]
                if expires > self.clock():
                    return data
            group = "quote" if path.startswith("marketfeed/") else "other"
            interval = 1.05 if group == "quote" else 0.25
            async with httpx.AsyncClient(base_url=BASE, timeout=30, follow_redirects=False,
                                         trust_env=False, transport=self.transport,
                                         headers={"access-token": token, "client-id": client_id}) as http:
                for attempt in range(3):
                    await self.sleep(max(0, self.last.get(group, -100) + interval - self.clock()))
                    self.last[group] = self.clock()
                    try:
                        kwargs = {"json": payload} if payload is not None else {}
                        response = await http.request(READS[path], path, **kwargs)
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt == 2:
                            raise BrokerError("Dhan network unavailable") from None
                        await self.sleep(2 ** attempt)
                        continue
                    if response.status_code in (401, 403):
                        raise BrokerError("Dhan credentials invalid, expired or not entitled")
                    if response.status_code in (429, 500, 502, 503, 504):
                        if attempt == 2:
                            raise BrokerError("Dhan temporarily unavailable or rate limited")
                        try:
                            delay = max(2 ** attempt, float(response.headers.get("Retry-After", "0")))
                        except ValueError:
                            delay = 2 ** attempt
                        if delay > 30:
                            raise BrokerError("Dhan rate limit: retry later")
                        await self.sleep(delay)
                        continue
                    if response.status_code >= 300:
                        raise BrokerError(f"Dhan rejected request (HTTP {response.status_code})")
                    try:
                        data = response.json()
                    except ValueError:
                        raise BrokerError("Invalid Dhan response") from None
                    if isinstance(data, dict) and str(data.get("status", "")).lower() in ("failure", "failed", "error"):
                        raise BrokerError("Dhan returned an API error")
                    if ttl:
                        self.cache[key] = (self.clock() + ttl, data)
                        self.cache.move_to_end(key)
                        while len(self.cache) > 256:
                            self.cache.popitem(last=False)
                    return data
        raise BrokerError("Dhan request failed")

    async def search(self, query: str, limit: int = 20):
        query = query.strip().casefold()
        if not query or not 1 <= limit <= 50:
            raise ValueError("Query required; limit must be 1–50")
        async with self.lock:
            if self.master is None or self.master[0] < self.clock():
                async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False,
                                             transport=self.transport) as http:
                    async with http.stream("GET", MASTER) as response:
                        if response.status_code != 200:
                            raise BrokerError("Instrument master unavailable")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > 80_000_000:
                                raise BrokerError("Instrument master too large")
                reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
                rows = []
                for row in reader:
                    symbol = row.get("SEM_TRADING_SYMBOL", "")
                    name = row.get("SEM_CUSTOM_SYMBOL", "")
                    sid = row.get("SEM_SMST_SECURITY_ID", "")
                    exchange = row.get("SEM_EXM_EXCH_ID", "")
                    segment = row.get("SEM_SEGMENT", "")
                    if sid and symbol:
                        rows.append({"symbol": symbol, "name": name, "security_id": sid,
                                     "exchange": exchange, "segment": segment,
                                     "instrument": row.get("SEM_INSTRUMENT_NAME", "")})
                if not rows:
                    raise BrokerError("Unexpected instrument master format")
                self.master = (self.clock() + 21600, rows)
            matches = []
            for row in self.master[1]:
                sym, name, sid = row["symbol"].casefold(), row["name"].casefold(), row["security_id"]
                if query in sym or query in name or query == sid:
                    rank = 0 if query in (sym, sid) else 1 if sym.startswith(query) else 2
                    matches.append((rank, row))
            matches.sort(key=lambda x: (x[0], x[1]["symbol"]))
            return [row for _, row in matches[:limit]]
