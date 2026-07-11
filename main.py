import asyncio
import base64
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


CODE_VERSION = "instagram_browser_page_api_v3_20260711"
PROFILE_API_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
INSTAGRAM_HOME_URL = "https://www.instagram.com/"
PROFILE_API_HEADERS = {
    "X-ASBD-ID": "198387",
    "X-IG-App-ID": "936619743392459",
    "X-IG-WWW-Claim": "0",
}
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
USERNAME_URL_RE = re.compile(r"(?:www\.)?instagram\.com/([A-Za-z0-9._]{1,30})", re.IGNORECASE)
PAGE_DIE_NEEDLES = (
    "profile isn't available",
    "sorry, this page isn't available",
    "page isn't available",
    "page not found",
    "may have been removed",
)
PAGE_LIVE_NEEDLES = ("instagram photos and videos", " followers", " following")


class IgCheckRequest(BaseModel):
    username: str | None = None
    url: str | None = None
    timeoutMs: int = Field(default=10000, ge=1500, le=20000)
    usePageFallback: bool = True
    debug: bool = False


class IgBulkRequest(BaseModel):
    items: list[IgCheckRequest] = Field(default_factory=list, max_length=500)
    concurrency: int = Field(default=3, ge=1, le=8)
    deadlineMs: int = Field(default=45000, ge=3000, le=55000)


app = FastAPI(title="Instagram Checker Service")
_startup_lock = asyncio.Lock()
_refresh_lock = asyncio.Lock()
_rate_lock = asyncio.Lock()
_playwright: Any = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_api_page: Page | None = None
_request_semaphore: asyncio.Semaphore | None = None
_cookie_count = 0
_session_ready_at = 0.0
_cooldown_until = 0.0
_request_count = 0
_live_count = 0
_die_count = 0
_unknown_count = 0
_last_error = ""
_next_request_at = 0.0


def _truthy(value: str | None, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _require_api_key(x_api_key: str | None) -> None:
    expected = str(os.getenv("IG_CHECKER_API_KEY") or "").strip()
    if expected and not hmac.compare_digest(expected, str(x_api_key or "").strip()):
        raise HTTPException(status_code=403, detail="invalid_api_key")


def _normalize_username(req: IgCheckRequest) -> str:
    raw = str(req.username or "").strip()
    if not raw:
        match = USERNAME_URL_RE.search(str(req.url or "").strip())
        raw = match.group(1) if match else ""
    raw = raw.lstrip("@").strip().strip("/").split("?", 1)[0].split("#", 1)[0]
    return raw.lower() if USERNAME_RE.fullmatch(raw) else ""


def _read_env_or_file(env_name: str, file_env_name: str) -> str:
    value = str(os.getenv(env_name) or "").strip()
    if value:
        return value
    path = str(os.getenv(file_env_name) or "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8-sig").strip()
    except Exception:
        return ""


def _parse_cookie_text(raw: str) -> tuple[list[dict[str, Any]], str]:
    cookies: list[dict[str, Any]] = []
    user_agent = ""
    for part in raw.strip().strip('"').strip("'").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        if name == "_uafec":
            user_agent = unquote(value)
            continue
        if name == "useragent":
            try:
                user_agent = base64.b64decode(value).decode("utf-8", "ignore").strip()
            except Exception:
                pass
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".instagram.com",
                "path": "/",
                "secure": True,
                "httpOnly": name in {"sessionid", "datr", "rur", "ig_did", "ps_l", "ps_n"},
            }
        )
    return cookies, user_agent


def _parse_cookie_json(raw: str) -> tuple[list[dict[str, Any]], str]:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return [], ""
    cookies: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name, value = str(item.get("name") or "").strip(), str(item.get("value") or "").strip()
        if not name or not value:
            continue
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": str(item.get("domain") or ".instagram.com"),
            "path": str(item.get("path") or "/"),
            "secure": bool(item.get("secure", True)),
            "httpOnly": bool(item.get("httpOnly", False)),
        }
        same_site = str(item.get("sameSite") or "").lower()
        if same_site in {"lax", "strict", "none", "no_restriction"}:
            cookie["sameSite"] = "None" if same_site in {"none", "no_restriction"} else same_site.capitalize()
        cookies.append(cookie)
    return cookies, ""


def _load_instagram_cookies() -> tuple[list[dict[str, Any]], str]:
    raw_json = _read_env_or_file("IG_COOKIES_JSON", "IG_COOKIES_JSON_PATH")
    if raw_json:
        try:
            return _parse_cookie_json(raw_json)
        except Exception:
            pass
    raw_text = _read_env_or_file("IG_COOKIE_TEXT", "IG_COOKIE_TEXT_PATH")
    return _parse_cookie_text(raw_text) if raw_text else ([], "")


def _normalize_proxy(raw: str | None) -> dict[str, str] | None:
    value = str(raw or os.getenv("IG_PROXY") or "").strip()
    if not value:
        return None
    if "://" not in value:
        parts = value.split(":")
        if len(parts) >= 4:
            return {
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2],
                "password": ":".join(parts[3:]),
            }
        return {"server": f"http://{value}"}
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "socks5", "socks4"} and parsed.hostname and parsed.port:
        proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy
    return None


async def _create_context() -> tuple[BrowserContext, Page]:
    global _cookie_count
    assert _browser is not None
    cookies, cookie_ua = _load_instagram_cookies()
    kwargs: dict[str, Any] = {"locale": "en-US"}
    # Prefer Chromium's native User-Agent so the HTTP/TLS/browser fingerprint
    # remains internally consistent. Only override it when the cookie export
    # explicitly carries the UA that created that session.
    configured_ua = cookie_ua or str(os.getenv("IG_USER_AGENT") or "").strip()
    if configured_ua:
        kwargs["user_agent"] = configured_ua
    proxy = _normalize_proxy(None)
    if proxy:
        kwargs["proxy"] = proxy
    context = await _browser.new_context(**kwargs)
    if cookies and _truthy(os.getenv("IG_PRIMARY_WITH_COOKIE"), False):
        await context.add_cookies(cookies)
    _cookie_count = len(cookies)
    page = await context.new_page()
    await page.goto(INSTAGRAM_HOME_URL, wait_until="domcontentloaded", timeout=20000)
    return context, page


async def _ensure_session() -> BrowserContext:
    global _playwright, _browser, _context, _api_page, _request_semaphore, _session_ready_at, _last_error
    if _context is not None:
        return _context
    async with _startup_lock:
        if _context is not None:
            return _context
        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
            _context, _api_page = await _create_context()
            # One request at a time per browser session is intentional. Instagram
            # starts returning intermittent 401 responses when the same anonymous
            # session sends concurrent profile API calls. Scale with more service
            # instances instead of sharing one session concurrently.
            concurrency = max(1, min(int(os.getenv("IG_CHECK_CONCURRENCY", "1") or "1"), 1))
            _request_semaphore = asyncio.Semaphore(concurrency)
            _session_ready_at = time.time()
            _last_error = ""
            return _context
        except Exception as exc:
            _last_error = f"session_start_failed:{type(exc).__name__}:{str(exc)[:180]}"
            raise


async def _refresh_session(reason: str) -> BrowserContext:
    global _context, _api_page, _session_ready_at, _last_error
    async with _refresh_lock:
        old = _context
        _last_error = reason[:220]
        assert _browser is not None
        replacement, replacement_page = await _create_context()
        _context = replacement
        _api_page = replacement_page
        _session_ready_at = time.time()
        if old is not None and old is not replacement:
            asyncio.create_task(_close_context_later(old))
        return replacement


async def _close_context_later(context: BrowserContext, delay_sec: float = 20.0) -> None:
    # Existing bulk requests may still reference the old context. Delayed close
    # prevents one auth refresh from cancelling unrelated in-flight checks.
    await asyncio.sleep(delay_sec)
    try:
        await context.close()
    except Exception:
        pass


async def _api_probe(page: Page, username: str, timeout_ms: int) -> dict[str, Any]:
    await _wait_for_rate_slot()
    started = time.perf_counter()
    try:
        response = await page.evaluate(
            """
            async ({ endpoint, username, headers, timeoutMs }) => {
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), timeoutMs);
              try {
                const url = `${endpoint}?username=${encodeURIComponent(username)}`;
                const response = await fetch(url, {
                  method: "GET",
                  credentials: "include",
                  headers,
                  signal: controller.signal,
                });
                let payload = {};
                try { payload = await response.json(); } catch (_) { payload = {}; }
                const user = payload && payload.data && payload.data.user
                  ? payload.data.user
                  : null;
                return {
                  http: response.status,
                  user,
                  message: String((payload && (payload.message || payload.error_type)) || ""),
                };
              } finally {
                clearTimeout(timer);
              }
            }
            """,
            {
                "endpoint": PROFILE_API_URL,
                "username": username,
                "headers": PROFILE_API_HEADERS,
                "timeoutMs": timeout_ms,
            },
        )
        http = int(response.get("http") or 0)
        user = response.get("user") if isinstance(response.get("user"), dict) else None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if http == 200 and isinstance(user, dict) and str(user.get("id") or "").strip():
            return {
                "status": "LIVE",
                "reason": "profile_api_user_found",
                "http": http,
                "elapsedMs": elapsed_ms,
                "user": user,
            }
        if http == 404:
            return {
                "status": "DIE",
                "reason": "profile_api_404",
                "http": http,
                "elapsedMs": elapsed_ms,
                "user": None,
            }
        message = str(response.get("message") or "")
        return {
            "status": "UNKNOWN",
            "reason": f"profile_api_http_{http}",
            "http": http,
            "elapsedMs": elapsed_ms,
            "user": None,
            "error": message[:180],
        }
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "reason": "profile_api_exception",
            "http": 0,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
            "user": None,
            "error": f"{type(exc).__name__}:{str(exc)[:180]}",
        }


async def _wait_for_rate_slot() -> None:
    global _next_request_at
    min_interval_ms = max(250, min(int(os.getenv("IG_MIN_REQUEST_INTERVAL_MS", "2200") or "2200"), 10000))
    async with _rate_lock:
        now = time.monotonic()
        if _next_request_at > now:
            await asyncio.sleep(_next_request_at - now)
        _next_request_at = time.monotonic() + (min_interval_ms / 1000)


async def _route_page_light(route: Any) -> None:
    if route.request.resource_type in {"image", "media", "font"}:
        await route.abort()
    else:
        await route.continue_()


async def _page_fallback(context: BrowserContext, username: str, timeout_ms: int) -> dict[str, Any]:
    started = time.perf_counter()
    page = await context.new_page()
    try:
        await page.route("**/*", _route_page_light)
        response = await page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(min(1800, max(500, timeout_ms // 4)))
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=min(timeout_ms, 2500))
        og_description = ""
        og = page.locator('meta[property="og:description"]')
        if await og.count():
            og_description = str(await og.first.get_attribute("content") or "")
        hay = f"{title}\n{text}\n{og_description}".lower()
        status = "DIE" if any(item in hay for item in PAGE_DIE_NEEDLES) else (
            "LIVE" if any(item in hay for item in PAGE_LIVE_NEEDLES) else "UNKNOWN"
        )
        return {
            "status": status,
            "reason": "page_fallback_signal" if status != "UNKNOWN" else "page_fallback_no_signal",
            "http": response.status if response else 0,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
            "title": title,
            "ogDescription": og_description[:300],
            "textSnippet": re.sub(r"\s+", " ", text).strip()[:400],
        }
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "reason": "page_fallback_exception",
            "http": 0,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}:{str(exc)[:180]}",
        }
    finally:
        await page.close()


def _format_result(username: str, probe: dict[str, Any], elapsed_ms: int, debug: bool) -> dict[str, Any]:
    global _request_count, _live_count, _die_count, _unknown_count
    status = str(probe.get("status") or "UNKNOWN").upper()
    user = probe.get("user") if isinstance(probe.get("user"), dict) else {}
    _request_count += 1
    if status == "LIVE":
        _live_count += 1
    elif status == "DIE":
        _die_count += 1
    else:
        _unknown_count += 1
    result: dict[str, Any] = {
        "ok": status in {"LIVE", "DIE"},
        "status": status,
        "username": str(user.get("username") or username),
        "instagramUserId": str(user.get("id") or ""),
        "fullName": str(user.get("full_name") or ""),
        "isPrivate": bool(user.get("is_private", False)) if user else None,
        "isVerified": bool(user.get("is_verified", False)) if user else None,
        "profilePicUrl": str(user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""),
        "url": f"https://www.instagram.com/{username}/",
        "source": "instagram_browser_session_api",
        "reason": str(probe.get("reason") or "unknown"),
        "http": int(probe.get("http") or 0),
        "elapsedMs": elapsed_ms,
        "codeVersion": CODE_VERSION,
    }
    if debug:
        result["debug"] = {key: value for key, value in probe.items() if key not in {"user"}}
    return result


async def _check_one(req: IgCheckRequest) -> dict[str, Any]:
    global _cooldown_until, _last_error, _api_page
    username = _normalize_username(req)
    started = time.perf_counter()
    if not username:
        return _format_result("", {"status": "UNKNOWN", "reason": "invalid_username", "http": 0}, 0, req.debug)
    if time.time() < _cooldown_until:
        if not req.usePageFallback:
            return _format_result(
                username,
                {"status": "UNKNOWN", "reason": "service_rate_limit_cooldown", "http": 429},
                int((time.perf_counter() - started) * 1000),
                req.debug,
            )
        context = await _ensure_session()
        fallback = await _page_fallback(context, username, min(req.timeoutMs, 10000))
        fallback["reason"] = (
            "page_fallback_during_api_cooldown"
            if fallback.get("status") in {"LIVE", "DIE"}
            else "page_fallback_cooldown_no_signal"
        )
        return _format_result(username, fallback, int((time.perf_counter() - started) * 1000), req.debug)
    context = await _ensure_session()
    assert _request_semaphore is not None
    async with _request_semaphore:
        if _api_page is None:
            return _format_result(
                username,
                {"status": "UNKNOWN", "reason": "api_page_not_ready", "http": 0},
                int((time.perf_counter() - started) * 1000),
                req.debug,
            )
        probe = await _api_probe(_api_page, username, req.timeoutMs)
        http = int(probe.get("http") or 0)
        if http in {401, 403}:
            # Instagram occasionally rejects the first anonymous browser fetch
            # while the next request on the same page succeeds. Retry once on
            # the same real browser session before rotating the context.
            probe = await _api_probe(_api_page, username, req.timeoutMs)
            http = int(probe.get("http") or 0)
            if int(probe.get("http") or 0) in {401, 403}:
                _cooldown_until = time.time() + max(30, min(int(os.getenv("IG_AUTH_COOLDOWN_SEC", "90") or "90"), 900))
                _last_error = f"profile_api_http_{int(probe.get('http') or 0)}"
        if http == 429:
            _cooldown_until = time.time() + max(15, min(int(os.getenv("IG_429_COOLDOWN_SEC", "60") or "60"), 600))
            _last_error = "profile_api_429"
        if probe.get("status") == "UNKNOWN" and req.usePageFallback:
            fallback = await _page_fallback(context, username, min(req.timeoutMs, 10000))
            if fallback.get("status") in {"LIVE", "DIE"}:
                fallback["reason"] = "page_fallback_after_api_unknown"
                probe = fallback
        return _format_result(
            username,
            probe,
            int((time.perf_counter() - started) * 1000),
            req.debug,
        )


@app.on_event("startup")
async def startup() -> None:
    if _truthy(os.getenv("IG_WARM_ON_START"), True):
        asyncio.create_task(_ensure_session())


@app.on_event("shutdown")
async def shutdown() -> None:
    global _playwright, _browser, _context, _api_page
    if _context is not None:
        await _context.close()
        _context = None
        _api_page = None
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "instagram-checker-service",
        "codeVersion": CODE_VERSION,
        "browserStarted": _browser is not None,
        "sessionReady": _context is not None,
        "apiPageReady": _api_page is not None and not _api_page.is_closed(),
        "sessionAgeSec": int(max(0, time.time() - _session_ready_at)) if _session_ready_at else 0,
        "cookieCount": _cookie_count,
        "primaryUsesCookie": _truthy(os.getenv("IG_PRIMARY_WITH_COOKIE"), False),
        "proxyConfigured": bool(_normalize_proxy(None)),
        "cooldownSec": int(max(0, _cooldown_until - time.time())),
        "requestCount": _request_count,
        "liveCount": _live_count,
        "dieCount": _die_count,
        "unknownCount": _unknown_count,
        "lastError": _last_error,
    }


@app.post("/ig/check")
async def check(req: IgCheckRequest, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    return await _check_one(req)


@app.post("/ig/check-bulk")
async def check_bulk(req: IgBulkRequest, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    started = time.perf_counter()
    if not req.items:
        return {"ok": True, "total": 0, "processed": 0, "deferred": 0, "results": []}
    local_sem = asyncio.Semaphore(req.concurrency)
    results: list[dict[str, Any] | None] = [None] * len(req.items)

    async def guarded(index: int, item: IgCheckRequest) -> tuple[int, dict[str, Any]]:
        async with local_sem:
            try:
                return index, await _check_one(item)
            except Exception as exc:
                username = _normalize_username(item)
                return index, _format_result(
                    username,
                    {
                        "status": "UNKNOWN",
                        "reason": "bulk_item_exception",
                        "http": 0,
                        "error": f"{type(exc).__name__}:{str(exc)[:180]}",
                    },
                    int((time.perf_counter() - started) * 1000),
                    item.debug,
                )

    tasks = [asyncio.create_task(guarded(index, item)) for index, item in enumerate(req.items)]
    deadline_sec = req.deadlineMs / 1000
    try:
        for completed in asyncio.as_completed(tasks, timeout=deadline_sec):
            index, result = await completed
            results[index] = result
    except asyncio.TimeoutError:
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    for index, item in enumerate(req.items):
        if results[index] is None:
            username = _normalize_username(item)
            results[index] = _format_result(
                username,
                {"status": "UNKNOWN", "reason": "bulk_deadline_deferred", "http": 0},
                int((time.perf_counter() - started) * 1000),
                item.debug,
            )
    complete_results = [item for item in results if item is not None]
    deferred = sum(1 for item in complete_results if item.get("reason") == "bulk_deadline_deferred")
    return {
        "ok": True,
        "partial": deferred > 0,
        "total": len(complete_results),
        "processed": len(complete_results) - deferred,
        "deferred": deferred,
        "live": sum(1 for item in complete_results if item.get("status") == "LIVE"),
        "die": sum(1 for item in complete_results if item.get("status") == "DIE"),
        "unknown": sum(1 for item in complete_results if item.get("status") == "UNKNOWN"),
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "codeVersion": CODE_VERSION,
        "results": complete_results,
    }


@app.post("/ig/session/refresh")
async def refresh_session(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    await _ensure_session()
    await _refresh_session("manual_refresh")
    return {"ok": True, "codeVersion": CODE_VERSION, "sessionReady": True}
