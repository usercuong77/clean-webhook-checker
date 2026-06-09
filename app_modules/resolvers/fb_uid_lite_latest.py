#!/usr/bin/env python3
"""
FB UID RESOLVER — LITE EDITION v6.0
====================================
Mục tiêu: CỰC NHẸ — RAM thấp — CPU thấp — đa luồng cao — chính xác — nhanh.

Tối ưu so với v5:
  ✂  Bỏ http2 (giảm RAM ~30%, ít CPU encrypt)
  ✂  Bỏ middleware, metrics history, JSON logging nặng
  ✂  Bỏ circuit breaker / queue (single async loop là đủ)
  ✂  Bỏ retry phức tạp (chỉ 1 retry nhẹ)
  ✂  Cache LRU tối giản (1000 entry, không lock)
  ✂  Chỉ 2 strategy chính: about + profile (bỏ graph/plugin chậm/ít dùng)
  ✂  Compile regex 1 lần, sort theo score
  ✂  Stream chunked nhỏ (8KB), early stop ngay khi score=100
  ✂  1 client httpx duy nhất, connection pool keep-alive
  ✂  Tận dụng asyncio.gather concurrency cao (200+ luồng)
  ✂  Endpoint /resolve, /batch, /health — đơn giản, nhanh

Footprint: ~25-40MB RAM, ~0.1 vCPU idle, 200+ concurrent ok.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from app_modules.resolvers.facebook_cookies import cookie_header, load_cookie_accounts

try:
    from fastapi import FastAPI
    from pydantic import BaseModel, Field
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


# ───── CONFIG (env-driven) ─────────────────────────────────────────────────
_ACTIVE_COOKIE_USERS: set[str] = set()

# Tunables — chỉnh cho phù hợp với free tier RAM thấp
CONCURRENCY     = int(os.environ.get("CONCURRENCY",     50))   # số luồng đồng thời
TIMEOUT_S       = float(os.environ.get("TIMEOUT_S",     8.0))  # timeout ngắn
MAX_BYTES       = int(os.environ.get("MAX_BYTES",       500_000))  # 500KB / page
CHUNK_SIZE      = int(os.environ.get("CHUNK_SIZE",      8192))
CACHE_MAX       = int(os.environ.get("CACHE_MAX",       1000))
CACHE_TTL       = int(os.environ.get("CACHE_TTL",       86400))
BATCH_LIMIT     = int(os.environ.get("BATCH_LIMIT",     500))
RETRY_ONCE      = os.environ.get("RETRY_ONCE", "1") == "1"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
UA_MOB = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
          "Mobile/15E148 Safari/604.1")

INVALID_BASE = {"0", "1"}
SUSPICIOUS_PREFIX = ("1032", "1033", "1034")


# ───── PATTERNS — sort sẵn theo score giảm dần ─────────────────────────────
_RAW_PATTERNS: List[Tuple[str, int, str]] = [
    ("userVanity_userID",  100, r'"userVanity"\s*:\s*"[^"]+"\s*,\s*"userID"\s*:\s*"(\d+)"'),
    ("userID_userVanity",  100, r'"userID"\s*:\s*"(\d+)"\s*,\s*"userVanity"\s*:\s*"[^"]+"'),
    ("profile_owner",       95, r'"profile_owner"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"'),
    ("pageOwner",           95, r'"pageOwner"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"'),
    ("user_typed",          92, r'"id"\s*:\s*"(\d+)"\s*,\s*"__typename"\s*:\s*"(?:User|Profile)"'),
    ("page_typed",          90, r'"id"\s*:\s*"(\d+)"\s*,\s*"__typename"\s*:\s*"(?:Page|Group)"'),
    ("og_fb_meta",          87, r'content="fb://profile/(\d+)"'),
    ("profileID",           85, r'"profileID"\s*:\s*"(\d+)"'),
    ("entity_id",           82, r'"entity_id"\s*:\s*"(\d+)"'),
    ("fb_scheme",           80, r'fb://profile/(\d+)'),
    ("profile_php_id",      78, r'profile\.php\?id=(\d+)'),
    ("userID_generic",      70, r'"userID"\s*:\s*"(\d+)"'),
    ("actorID",             50, r'"actorID"\s*:\s*"(\d+)"'),
    ("pageID_weak",         40, r'"pageID"\s*:\s*"(\d+)"'),
]
PATTERNS = [(name, score, re.compile(rx))
            for name, score, rx in sorted(_RAW_PATTERNS, key=lambda x: -x[1])]


def find_uid(text: str) -> Optional[Tuple[str, str, int]]:
    """Trả về (uid, pattern_name, score) hoặc None. Dừng ngay khi gặp score=100."""
    best: Optional[Tuple[str, str, int, int]] = None
    for name, score, rx in PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        uid = m.group(1)
        if uid in _invalid_uid_values() or not uid.isdigit() or len(uid) > 20:
            continue
        if len(uid) == 10 and uid[0] in "678":   # timestamp-like
            continue
        s = score
        if len(uid) >= 17 and uid[:4] in SUSPICIOUS_PREFIX:
            s = min(s, 35)
        if best is None or s > best[2]:
            best = (uid, name, s, m.start())
            if s >= 100:
                break
    if best:
        return best[0], best[1], best[2]
    return None


def _invalid_uid_values() -> set[str]:
    return INVALID_BASE | {uid for uid in _ACTIVE_COOKIE_USERS if uid}


def _load_bot_cookie_header() -> tuple[str, str]:
    accounts = [account for account in load_cookie_accounts() if account.is_usable]
    if not accounts:
        return "", ""
    account = accounts[0]
    if account.c_user:
        _ACTIVE_COOKIE_USERS.add(account.c_user)
    return cookie_header(account), account.browser_user_agent


# ───── LRU CACHE (OrderedDict, không cần lock cho asyncio single-thread) ───
_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()


def cache_get(k: str) -> Optional[Dict[str, Any]]:
    v = _cache.get(k)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > CACHE_TTL:
        _cache.pop(k, None)
        return None
    _cache.move_to_end(k)
    return data


def cache_set(k: str, data: Dict[str, Any]) -> None:
    _cache[k] = (time.time(), data)
    _cache.move_to_end(k)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


# ───── NORMALIZE ───────────────────────────────────────────────────────────
_RESERVED = {
    "login", "logout", "home", "search", "groups", "events", "pages",
    "people", "marketplace", "gaming", "watch", "help", "ads", "business",
    "share", "sharer", "dialog", "ajax", "feed", "notifications", "messages",
    "profile.php", "about", "privacy", "settings", "checkpoint", "recover",
}


def normalize(raw: str) -> Dict[str, Any]:
    raw = raw.strip().rstrip("/")
    if not raw:
        return {"error": "empty"}
    if raw.isdigit():
        return {"direct_uid": raw}
    if not raw.startswith(("http://", "https://")):
        raw = "https://www.facebook.com/" + raw
    p = urlparse(raw)
    host = p.netloc.lower()
    if "facebook.com" not in host and "fb.com" not in host:
        return {"error": f"not_fb:{raw}"}
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 2 and parts[0] == "share":
        return {"share_code": parts[1], "share_url": raw}
    if parts and parts[0] == "profile.php":
        uid = parse_qs(p.query).get("id", [""])[0]
        if uid.isdigit():
            return {"direct_uid": uid}
    if parts and parts[0].lower() not in _RESERVED:
        slug = parts[0]   # giữ nguyên case
        enc = quote(slug, safe="-._~")
        return {
            "slug":     slug,
            "about":    f"https://www.facebook.com/{enc}/about",
            "profile":  f"https://www.facebook.com/{enc}",
            "mobile":   f"https://m.facebook.com/{enc}",
        }
    return {"error": f"no_slug:{raw}"}


# ───── LOGIN-REDIRECT DETECT (tối giản) ────────────────────────────────────
_LOGIN_RX = re.compile(r'action="https://www\.facebook\.com/login|<title>Facebook</title>')


def is_login(text: str, final_url: str) -> bool:
    if "/login" in final_url or "/checkpoint" in final_url:
        return True
    return bool(_LOGIN_RX.search(text[:6000]))


# ───── SHARE LINK RESOLVE ──────────────────────────────────────────────────
_SLUG_RX = re.compile(r'facebook\.com/([A-Za-z0-9._%-]+?)(?:[/?]|$)')


async def resolve_share(client: httpx.AsyncClient, share_url: str) -> Optional[str]:
    # 1) no-redirect Location header
    try:
        r = await client.get(share_url, follow_redirects=False)
        loc = r.headers.get("location", "")
        if loc:
            m = _SLUG_RX.search(loc)
            if m:
                cand = unquote(m.group(1)).rstrip("/")
                if cand and cand.lower() not in _RESERVED:
                    return cand
    except Exception:
        pass
    # 2) follow redirects, check final URL
    try:
        r = await client.get(share_url, follow_redirects=True)
        final = str(r.url)
        pp = urlparse(final)
        if "facebook.com" in pp.netloc:
            parts = [x for x in pp.path.split("/") if x]
            if parts and parts[0].lower() not in _RESERVED:
                cand = parts[0]
                return cand
            if pp.path.lstrip("/") == "profile.php":
                uid = parse_qs(pp.query).get("id", [""])[0]
                if uid.isdigit():
                    return uid
        body = r.text[:30_000]
        nm = re.search(r'[?&]next=([^&"\']+)', body)
        if nm:
            nv = unquote(nm.group(1))
            m = _SLUG_RX.search(nv)
            if m:
                cand = unquote(m.group(1)).rstrip("/")
                if cand and cand.lower() not in _RESERVED:
                    return cand
    except Exception:
        pass
    return None


# ───── STREAM FETCH — early-stop khi gặp score=100 ─────────────────────────
async def stream_for_uid(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES,
) -> Tuple[Optional[Tuple[str, str, int]], str, int]:
    """Trả về ((uid, pattern, score) | None, reason, status_code)."""
    buf = bytearray()
    try:
        async with client.stream("GET", url) as r:
            status = r.status_code
            final = str(r.url)
            if status != 200:
                return None, f"http_{status}", status
            best: Optional[Tuple[str, str, int]] = None
            async for chunk in r.aiter_bytes(CHUNK_SIZE):
                buf.extend(chunk)
                # check login redirect mỗi 8KB đầu
                if len(buf) <= 16_384:
                    text = buf.decode("utf-8", errors="replace")
                    if is_login(text, final):
                        return None, "login_redirect", status
                text = buf.decode("utf-8", errors="replace")
                hit = find_uid(text)
                if hit:
                    if best is None or hit[2] > best[2]:
                        best = hit
                    if hit[2] >= 100:
                        return best, "ok", status
                if len(buf) >= max_bytes:
                    break
            return (best, "ok" if best else "no_uid", status)
    except httpx.TimeoutException:
        return None, "timeout", 0
    except Exception as e:
        return None, f"err:{type(e).__name__}", 0


# ───── RESOLVE — pipeline siêu gọn ─────────────────────────────────────────
_sem: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(CONCURRENCY)
    return _sem


async def _try_url(client: httpx.AsyncClient, url: str,
                   ) -> Tuple[Optional[Tuple[str, str, int]], str, int]:
    res = await stream_for_uid(client, url)
    if res[0] is None and RETRY_ONCE and res[1] in ("timeout", "err:RemoteProtocolError",
                                                     "err:ConnectError", "err:ReadError"):
        await asyncio.sleep(0.3)
        res = await stream_for_uid(client, url)
    return res


async def resolve(client: httpx.AsyncClient, client_mob: httpx.AsyncClient,
                  value: str) -> Dict[str, Any]:
    t0 = time.time()
    key = value.strip().rstrip("/")

    cached = cache_get(key)
    if cached:
        return {**cached, "cached": True, "elapsed_ms": 0}

    info = normalize(value)
    if "error" in info:
        return {"success": False, "reason": info["error"], "input": value,
                "elapsed_ms": int((time.time() - t0) * 1000)}

    if "direct_uid" in info:
        out = {"success": True, "uid": info["direct_uid"], "strategy": "direct",
               "score": 100, "input": value, "cached": False, "elapsed_ms": 0}
        cache_set(key, out)
        return out

    # SHARE link
    if "share_code" in info:
        async with _semaphore():
            slug = await resolve_share(client, info["share_url"])
        if not slug:
            return {"success": False, "reason": "share_unresolvable",
                    "input": value, "elapsed_ms": int((time.time() - t0) * 1000)}
        if slug.isdigit():
            out = {"success": True, "uid": slug, "strategy": "share_direct",
                   "score": 100, "input": value, "resolved_slug": slug,
                   "cached": False, "elapsed_ms": int((time.time() - t0) * 1000)}
            cache_set(key, out)
            return out
        inner = await resolve(client, client_mob, slug)
        inner["input"]         = value
        inner["share_code"]    = info["share_code"]
        inner["resolved_slug"] = slug
        if "strategy" in inner:
            inner["strategy"] = f"share→{inner['strategy']}"
        inner["elapsed_ms"] = int((time.time() - t0) * 1000)
        if inner.get("success"):
            cache_set(key, inner)
        return inner

    # SLUG — profile + about chạy SONG SONG: vừa nhanh vừa giữ accuracy tối đa
    async with _semaphore():
        profile_task = asyncio.create_task(_try_url(client, info["profile"]))
        about_task   = asyncio.create_task(_try_url(client, info["about"]))
        task_map = {profile_task: "profile", about_task: "about"}

        tried: List[str] = []
        winner: Optional[Tuple[str, str, int]] = None
        winner_strat = ""
        pending = {profile_task, about_task}

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                hit, reason, status = await t
                strat = task_map[t]
                tried.append(f"{strat}[{status}]:{reason if not hit else 'ok'}")
                if hit and (winner is None or hit[2] > winner[2]):
                    winner = hit
                    winner_strat = strat
                # score >= 95 coi như đủ tin cậy, hủy request còn lại để tiết kiệm bandwidth/RAM
                if hit and hit[2] >= 95:
                    for p in pending:
                        p.cancel()
                    pending = set()
                    break

    # mobile — fallback cuối cùng, chỉ khi cả 2 endpoint desktop vẫn yếu
    if winner is None or winner[2] < 50:
        async with _semaphore():
            hit3, reason3, status3 = await _try_url(client_mob, info["mobile"])
        tried.append(f"mobile[{status3}]:{reason3 if not hit3 else 'ok'}")
        if hit3 and (winner is None or hit3[2] > winner[2]):
            winner = hit3
            winner_strat = "mobile"

    elapsed = int((time.time() - t0) * 1000)

    if winner is None:
        return {"success": False, "reason": "no_uid", "tried": tried,
                "input": value, "elapsed_ms": elapsed}

    if winner[2] < 50:
        return {"success": False, "reason": f"low_confidence({winner[2]})",
                "tried": tried, "input": value, "elapsed_ms": elapsed,
                "candidate": winner[0]}

    out = {
        "success":    True,
        "uid":        winner[0],
        "strategy":   winner_strat,
        "pattern":    winner[1],
        "score":      winner[2],
        "elapsed_ms": elapsed,
        "input":      value,
        "cached":     False,
    }
    cache_set(key, out)
    return out


# ───── HTTP CLIENT — pool nhỏ, không http2 (tiết kiệm RAM) ─────────────────
def make_client(mobile: bool = False) -> httpx.AsyncClient:
    bot_cookie, browser_user_agent = _load_bot_cookie_header()
    headers = {
        "User-Agent":      UA_MOB if mobile else (browser_user_agent or UA),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Keep this to encodings httpx can decode without optional brotli/zstd extras.
        # If Facebook returns raw br bytes, regex scanning sees compressed garbage.
        "Accept-Encoding": "gzip, deflate",
        "Connection":      "keep-alive",
    }
    if bot_cookie:
        headers["Cookie"] = bot_cookie
    if not mobile:
        headers.update({
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        })
    return httpx.AsyncClient(
        headers=headers,
        # http2=False — quan trọng để giảm RAM/CPU
        timeout=httpx.Timeout(connect=5.0, read=TIMEOUT_S, write=5.0, pool=3.0),
        limits=httpx.Limits(
            max_keepalive_connections=min(CONCURRENCY, 30),
            max_connections=min(CONCURRENCY + 10, 60),
        ),
        follow_redirects=True,
    )


# ───── FASTAPI (tối giản) ──────────────────────────────────────────────────
if HAS_FASTAPI:

    _client: Optional[httpx.AsyncClient] = None
    _client_mob: Optional[httpx.AsyncClient] = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _client, _client_mob
        _client = make_client(mobile=False)
        _client_mob = make_client(mobile=True)
        try:
            yield
        finally:
            await _client.aclose()
            await _client_mob.aclose()

    app = FastAPI(
        title="FB UID Resolver Lite",
        version="6.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    class ResolveReq(BaseModel):
        input: str = Field(..., min_length=1, max_length=2048)

    class BatchReq(BaseModel):
        inputs: List[str] = Field(..., min_length=1, max_length=BATCH_LIMIT)

    @app.get("/")
    def root():
        return {
            "service":      "FB UID Resolver Lite",
            "version":      "6.0",
            "concurrency":  CONCURRENCY,
            "cache_size":   len(_cache),
            "cache_max":    CACHE_MAX,
            "endpoints":    ["/resolve", "/batch", "/health"],
        }

    @app.get("/health")
    def health():
        return {
            "status":     "ok",
            "cache":      len(_cache),
            "uptime_ok":  True,
        }

    @app.post("/resolve")
    async def api_resolve(req: ResolveReq):
        return await resolve(_client, _client_mob, req.input)

    @app.post("/batch")
    async def api_batch(req: BatchReq):
        results = await asyncio.gather(
            *[resolve(_client, _client_mob, v) for v in req.inputs],
            return_exceptions=False,
        )
        ok = sum(1 for r in results if r.get("success"))
        return {"total": len(results), "ok": ok, "results": results}

    @app.delete("/cache")
    def cache_clear():
        n = len(_cache)
        _cache.clear()
        return {"cleared": n}


# ───── CLI / BENCHMARK ─────────────────────────────────────────────────────
TEST_URLS = [
    "https://www.facebook.com/share/18xYWLM6ub/",
    "https://www.facebook.com/share/1KtLEotdXy/",
    "https://www.facebook.com/zuck",
    "https://www.facebook.com/Cristiano",
    "https://www.facebook.com/thanh.duyen.37570",
    "https://www.facebook.com/BillGates",
    "https://www.facebook.com/NASA",
    "https://www.facebook.com/LamQuocCuong.Vn",
    "https://www.facebook.com/sohelranacdtc",
    "https://www.facebook.com/nayem.khandker.5",
    "https://www.facebook.com/profile.php?id=100015771131121",
    "100044296486382",
]


async def benchmark(parallel: bool = True):
    print("=" * 60)
    print("  FB UID Resolver LITE v6 — Benchmark")
    print(f"  CONCURRENCY={CONCURRENCY}  TIMEOUT={TIMEOUT_S}s  "
          f"MAX_BYTES={MAX_BYTES//1024}KB")
    print(f"  Mode: {'PARALLEL' if parallel else 'SEQUENTIAL'}")
    print("=" * 60)

    async with make_client(False) as c, make_client(True) as cm:
        # warm-up
        try:
            await c.get("https://www.facebook.com/")
        except Exception:
            pass

        t0 = time.time()
        if parallel:
            results = await asyncio.gather(
                *[resolve(c, cm, u) for u in TEST_URLS]
            )
        else:
            results = []
            for u in TEST_URLS:
                results.append(await resolve(c, cm, u))
        total_ms = int((time.time() - t0) * 1000)

        ok = 0
        for u, r in zip(TEST_URLS, results):
            ms = r.get("elapsed_ms", 0)
            if r.get("success"):
                ok += 1
                extra = f" (slug:{r['resolved_slug']})" if r.get("resolved_slug") else ""
                print(f"  {u:<50} {r['uid']:<22} {ms:>5}ms  "
                      f"{r['strategy']}{extra}")
            else:
                print(f"  {u:<50} {'FAILED':<22} {ms:>5}ms  "
                      f"{r.get('reason')}")

        print("-" * 60)
        print(f"  {ok}/{len(TEST_URLS)} OK | wall {total_ms}ms | "
              f"avg per URL {total_ms // len(TEST_URLS)}ms")
        print()


async def stress_test(n: int = 100):
    """Test đa luồng — phóng N request song song."""
    print(f"Stress: {n} request song song với CONCURRENCY={CONCURRENCY}")
    urls = (TEST_URLS * (n // len(TEST_URLS) + 1))[:n]
    async with make_client(False) as c, make_client(True) as cm:
        try:
            await c.get("https://www.facebook.com/")
        except Exception:
            pass
        t0 = time.time()
        results = await asyncio.gather(*[resolve(c, cm, u) for u in urls])
        elapsed = time.time() - t0
        ok = sum(1 for r in results if r.get("success"))
        print(f"  {n} req trong {elapsed:.2f}s → {n/elapsed:.1f} req/s")
        print(f"  Success: {ok}/{n} ({100*ok//n}%)")
        latencies = sorted([r.get("elapsed_ms", 0) for r in results])
        if latencies:
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            print(f"  Latency p50={p50}ms p95={p95}ms p99={p99}ms")


async def single(url: str):
    async with make_client(False) as c, make_client(True) as cm:
        print(json.dumps(await resolve(c, cm, url), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "test":
        asyncio.run(single(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "serve":
        if not HAS_FASTAPI:
            print("ERROR: pip install fastapi uvicorn"); sys.exit(1)
        uvicorn.run("fb_uid_lite:app", host="0.0.0.0",
                    port=int(os.environ.get("PORT", 7860)), workers=1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "stress":
        n = int(sys.argv[2]) if len(sys.argv) >= 3 else 100
        asyncio.run(stress_test(n))
    elif len(sys.argv) >= 2 and sys.argv[1] == "seq":
        asyncio.run(benchmark(parallel=False))
    else:
        asyncio.run(benchmark(parallel=True))
