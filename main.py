import asyncio
import os
import re
import time
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

LIVE_NEEDLES = (
    "instagram photos and videos",
    " posts",
    " followers",
    " following",
)
DIE_NEEDLES = (
    "profile isn't available",
    "not available",
    "may be broken",
    "removed",
    "page not found",
)
USERNAME_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)")


class IgCheckRequest(BaseModel):
    username: str | None = None
    url: str | None = None
    timeoutMs: int = Field(default=8000, ge=1500, le=15000)
    detectMs: int = Field(default=1800, ge=400, le=5000)
    useCookieFallback: bool = True
    debug: bool = False


class IgBulkRequest(BaseModel):
    items: list[IgCheckRequest]
    concurrency: int = Field(default=3, ge=1, le=10)


app = FastAPI(title="Instagram Checker Service")
_startup_lock = asyncio.Lock()
_playwright: Any = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_semaphore: asyncio.Semaphore | None = None


def _normalize_username(req: IgCheckRequest) -> str:
    raw = (req.username or "").strip()
    if raw:
        raw = raw.lstrip("@").strip().strip("/")
        if raw:
            return raw
    url = (req.url or "").strip()
    match = USERNAME_RE.search(url)
    if match:
        return match.group(1).strip("/")
    return ""


def _classify(title: str, text: str) -> str:
    hay = f"{title}\n{text}".lower()
    if any(needle in hay for needle in DIE_NEEDLES):
        return "DIE"
    if any(needle in hay for needle in LIVE_NEEDLES):
        return "LIVE"
    return "UNKNOWN"


async def _route_light(route: Any) -> None:
    resource_type = route.request.resource_type
    if resource_type in {"image", "media", "font", "stylesheet"}:
        await route.abort()
        return
    await route.continue_()


async def _ensure_browser() -> BrowserContext:
    global _playwright, _browser, _context, _semaphore
    if _context is not None:
        return _context
    async with _startup_lock:
        if _context is not None:
            return _context
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-sandbox",
            ],
        )
        _context = await _browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 900, "height": 800},
        )
        await _context.route("**/*", _route_light)
        # Instagram sometimes returns an empty app shell under concurrent checks,
        # which can look like an unavailable profile. Keep one page active per
        # free instance and scale by adding instances instead.
        concurrency = int(os.getenv("IG_CHECK_CONCURRENCY", "1") or "1")
        _semaphore = asyncio.Semaphore(max(1, min(concurrency, 1)))
        return _context


async def _detect(page: Page, detect_ms: int) -> tuple[str, str, str]:
    title = ""
    text = ""
    deadline = time.perf_counter() + (detect_ms / 1000)
    while time.perf_counter() < deadline:
        try:
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=250)
        except Exception:
            await asyncio.sleep(0.05)
            continue
        status = _classify(title, text)
        if status != "UNKNOWN":
            return status, title, text
        await asyncio.sleep(0.05)
    if title.strip().lower() == "instagram" and not text.strip():
        return "DIE", title, text
    return _classify(title, text), title, text


async def _check_one(req: IgCheckRequest) -> dict[str, Any]:
    username = _normalize_username(req)
    start = time.perf_counter()
    if not username:
        return {
            "ok": False,
            "status": "UNKNOWN",
            "reason": "missing_username",
            "elapsedMs": int((time.perf_counter() - start) * 1000),
        }
    context = await _ensure_browser()
    assert _semaphore is not None
    async with _semaphore:
        page = await context.new_page()
        err = ""
        http_status = 0
        try:
            response = await page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="commit",
                timeout=req.timeoutMs,
            )
            http_status = response.status if response is not None else 0
            status, title, text = await _detect(page, req.detectMs)
            payload = {
                "ok": status in {"LIVE", "DIE"},
                "status": status,
                "username": username,
                "url": f"https://www.instagram.com/{username}/",
                "source": "instagram_browser_light",
                "reason": "browser_signal" if status != "UNKNOWN" else "no_stable_signal",
                "http": http_status,
                "title": title,
                "elapsedMs": int((time.perf_counter() - start) * 1000),
            }
            if req.debug:
                payload["textSnippet"] = re.sub(r"\s+", " ", text).strip()[:700]
                try:
                    html = await page.content()
                    payload["htmlSnippet"] = re.sub(r"\s+", " ", html).strip()[:700]
                except Exception:
                    payload["htmlSnippet"] = ""
            return payload
        except Exception as exc:
            err = f"{type(exc).__name__}:{str(exc)[:160]}"
            return {
                "ok": False,
                "status": "UNKNOWN",
                "username": username,
                "url": f"https://www.instagram.com/{username}/",
                "source": "instagram_browser_light",
                "reason": "browser_exception",
                "error": err,
                "http": http_status,
                "elapsedMs": int((time.perf_counter() - start) * 1000),
            }
        finally:
            await page.close()


@app.on_event("shutdown")
async def shutdown() -> None:
    global _playwright, _browser, _context
    if _context is not None:
        await _context.close()
        _context = None
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
        "browserStarted": _context is not None,
    }


@app.post("/ig/check")
async def check(req: IgCheckRequest) -> dict[str, Any]:
    return await _check_one(req)


@app.post("/ig/check-bulk")
async def check_bulk(req: IgBulkRequest) -> dict[str, Any]:
    start = time.perf_counter()
    sem = asyncio.Semaphore(max(1, min(req.concurrency, 10)))

    async def guarded(item: IgCheckRequest) -> dict[str, Any]:
        async with sem:
            return await _check_one(item)

    results = await asyncio.gather(*(guarded(item) for item in req.items))
    return {
        "ok": True,
        "total": len(results),
        "live": sum(1 for item in results if item.get("status") == "LIVE"),
        "die": sum(1 for item in results if item.get("status") == "DIE"),
        "unknown": sum(1 for item in results if item.get("status") == "UNKNOWN"),
        "elapsedMs": int((time.perf_counter() - start) * 1000),
        "results": results,
    }
