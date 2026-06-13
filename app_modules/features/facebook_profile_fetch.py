from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

import requests

from app_modules.resolvers.facebook_cookies import cookie_header


@dataclass(frozen=True)
class FetchResult:
    http_code: int
    text: str
    final_url: str
    reason: str
    elapsed_ms: int


def public_headers() -> dict[str, str]:
    return {
        "User-Agent": "facebookcatalog/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def cookie_headers(account) -> dict[str, str]:
    user_agent = str(getattr(account, "browser_user_agent", "") or "").strip()
    headers = {
        "User-Agent": user_agent
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Cookie": cookie_header(account),
    }
    return headers


def fetch_limited_text(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    stop_markers: tuple[bytes, ...] = (),
    session: requests.Session | None = None,
) -> FetchResult:
    started = time.perf_counter()
    chunks: list[bytes] = []
    total = 0
    tail = b""
    try:
        client = session or requests
        with client.get(
            url,
            headers=dict(headers),
            timeout=_requests_timeout(timeout),
            allow_redirects=True,
            stream=True,
        ) as response:
            encoding = response.encoding or "utf-8"
            deadline = max(0.6, float(timeout))
            for chunk in response.iter_content(chunk_size=16_384):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if stop_markers:
                    scan = (tail + chunk).lower()
                    if any(marker in scan for marker in stop_markers):
                        break
                    tail = scan[-2048:]
                if total >= max_bytes:
                    break
                if time.perf_counter() - started >= deadline:
                    break
            text = b"".join(chunks).decode(encoding, errors="ignore")
            return FetchResult(
                http_code=response.status_code,
                text=text,
                final_url=response.url or url,
                reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
    except requests.RequestException as exc:
        return FetchResult(
            http_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{type(exc).__name__}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


def _requests_timeout(timeout: float) -> tuple[float, float]:
    read_timeout = max(0.8, float(timeout))
    connect_timeout = max(0.4, min(0.9, read_timeout / 2))
    return (connect_timeout, read_timeout)
