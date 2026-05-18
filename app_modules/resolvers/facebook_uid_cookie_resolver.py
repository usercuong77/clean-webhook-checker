from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import requests

from app_modules.core.config import get_config
from app_modules.resolvers.facebook_cookies import cookie_header, load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import (
    build_facebook_navigation_hint_headers,
    build_facebook_probe_urls,
    extract_username_from_url,
    extract_uid_candidates_from_html,
    extract_uid_from_html,
    extract_uid_from_url,
)

COOKIE_UID_USER_AGENTS = (
    (
        "Mozilla/5.0 (Linux; U; Android 4.0.3; en-us; Galaxy Nexus Build/IML74K) "
        "AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30"
    ),
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
)
DEFAULT_COOKIE_UID_TIMEOUT_SEC = 2.5
DEFAULT_COOKIE_UID_DEADLINE_SEC = 7.0
DEFAULT_COOKIE_UID_MAX_ACCOUNTS = 2
DEFAULT_COOKIE_UID_MAX_REQUESTS = 8


@dataclass(frozen=True)
class CookieUidResolution:
    uid: str
    source: str
    reason: str
    probes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.uid)


@dataclass(frozen=True)
class CookieFetchResult:
    http_code: int
    text: str
    final_url: str
    reason: str


def resolve_uid_with_cookies(raw: Any) -> CookieUidResolution:
    probe_urls = _cookie_probe_urls(raw)
    if not probe_urls:
        return CookieUidResolution("", "uid_cookie_resolver", "no_facebook_probe_urls")

    accounts = [account for account in load_cookie_accounts() if account.is_usable]
    if not accounts:
        return CookieUidResolution("", "uid_cookie_resolver", "no_usable_cookie_accounts")

    timeout = _cookie_uid_timeout()
    deadline_at = time.monotonic() + _cookie_uid_deadline()
    max_accounts = _cookie_uid_max_accounts()
    max_requests = _cookie_uid_max_requests()
    request_count = 0
    probes: list[dict[str, Any]] = []

    for account in accounts[:max_accounts]:
        for probe_url in probe_urls:
            for headers in _cookie_header_candidates(account):
                if request_count >= max_requests:
                    break
                request_timeout = _remaining_timeout(deadline_at, timeout)
                if request_timeout <= 0:
                    break

                request_count += 1
                fetch_result = _fetch_text_with_cookie(probe_url, headers, request_timeout)
                probe = {
                    "source": "uid_cookie_probe",
                    "url": probe_url,
                    "httpCode": fetch_result.http_code,
                    "finalUrl": fetch_result.final_url,
                    "reason": fetch_result.reason,
                    "cookieAccount": account.masked_id,
                    "cookieSource": account.source,
                    "cookieIndex": account.index,
                    "userAgent": _header_label(headers),
                    "timeoutSec": request_timeout,
                }

                uid_from_html = _extract_uid_from_cookie_html(fetch_result.text, account)
                if uid_from_html:
                    verification_timeout = _remaining_timeout(deadline_at, timeout)
                    if (
                        _needs_slug_verification(raw)
                        and (
                            verification_timeout <= 0
                            or not _verify_uid_matches_requested_slug(
                                uid_from_html,
                                raw,
                                headers,
                                verification_timeout,
                            )
                        )
                    ):
                        probe["candidateUid"] = uid_from_html
                        probe["reason"] = "uid_candidate_rejected_by_slug_verification"
                        probes.append(probe)
                        continue
                    probe["foundUid"] = uid_from_html
                    probe["reason"] = "uid_found_in_cookie_html"
                    probes.append(probe)
                    return CookieUidResolution(
                        uid_from_html,
                        "uid_cookie_probe",
                        "uid_found_in_cookie_html",
                        probes,
                    )

                uid_from_final_url = extract_uid_from_url(fetch_result.final_url)
                if uid_from_final_url:
                    probe["foundUid"] = uid_from_final_url
                    probe["reason"] = "uid_found_in_cookie_final_url"
                    probes.append(probe)
                    return CookieUidResolution(
                        uid_from_final_url,
                        "uid_cookie_probe",
                        "uid_found_in_cookie_final_url",
                        probes,
                    )

                probes.append(probe)
            if request_count >= max_requests or _remaining_timeout(deadline_at, timeout) <= 0:
                break
        if request_count >= max_requests or _remaining_timeout(deadline_at, timeout) <= 0:
            break

    return CookieUidResolution(
        "",
        "uid_cookie_resolver",
        "uid_not_found_after_cookie_probe_budget",
        probes,
    )


def _cookie_probe_urls(raw: Any) -> list[str]:
    urls = build_facebook_probe_urls(raw)
    return sorted(urls, key=_cookie_probe_url_priority)


def _cookie_probe_url_priority(url: str) -> tuple[int, str]:
    value = str(url or "").lower()
    if "mbasic.facebook.com" in value:
        return (0, value)
    if "m.facebook.com" in value:
        return (1, value)
    return (2, value)


def _cookie_header_candidates(account) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for user_agent in COOKIE_UID_USER_AGENTS:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Cookie": cookie_header(account),
        }
        headers.update(build_facebook_navigation_hint_headers(user_agent))
        out.append(headers)
    return out


def _extract_uid_from_cookie_html(text: str, account) -> str:
    for uid in extract_uid_candidates_from_html(text):
        if uid and uid != account.c_user:
            return uid

    uid = extract_uid_from_html(text)
    if uid and uid != account.c_user:
        return uid
    return ""


def _header_label(headers: Mapping[str, str]) -> str:
    user_agent = str(headers.get("User-Agent", "")).strip()
    if not user_agent:
        return "no_user_agent"
    return user_agent[:80]


def _fetch_text_with_cookie(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> CookieFetchResult:
    try:
        response = requests.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=True,
        )
        return CookieFetchResult(
            http_code=response.status_code,
            text=response.text or "",
            final_url=response.url or url,
            reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
        )
    except requests.RequestException as exc:
        return CookieFetchResult(
            http_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{type(exc).__name__}",
        )


def _cookie_uid_timeout() -> float:
    configured = max(0.5, get_config().request_timeout_seconds)
    return min(configured, _env_float("UID_COOKIE_PROBE_TIMEOUT_SEC", DEFAULT_COOKIE_UID_TIMEOUT_SEC))


def _cookie_uid_deadline() -> float:
    return _env_float("UID_COOKIE_PROBE_DEADLINE_SEC", DEFAULT_COOKIE_UID_DEADLINE_SEC)


def _cookie_uid_max_accounts() -> int:
    return _env_int("UID_COOKIE_PROBE_MAX_ACCOUNTS", DEFAULT_COOKIE_UID_MAX_ACCOUNTS)


def _cookie_uid_max_requests() -> int:
    return _env_int("UID_COOKIE_PROBE_MAX_REQUESTS", DEFAULT_COOKIE_UID_MAX_REQUESTS)


def _remaining_timeout(deadline_at: float, preferred_timeout: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        return 0.0
    return max(0.1, min(preferred_timeout, remaining))


def _env_float(key: str, default: float) -> float:
    try:
        value = float(os.getenv(key, "").strip() or default)
    except ValueError:
        value = default
    return max(0.1, value)


def _env_int(key: str, default: int) -> int:
    try:
        value = int(os.getenv(key, "").strip() or default)
    except ValueError:
        value = default
    return max(1, value)


def _needs_slug_verification(raw: Any) -> bool:
    return bool(extract_username_from_url(raw))


def _verify_uid_matches_requested_slug(
    uid: str,
    raw: Any,
    headers: Mapping[str, str],
    timeout: float,
) -> bool:
    slug = extract_username_from_url(raw).strip().lower()
    if not slug:
        return True

    fetch_result = _fetch_text_with_cookie(
        f"https://www.facebook.com/profile.php?id={uid}",
        headers,
        timeout,
    )
    final_url = str(fetch_result.final_url or "").lower()
    body = str(fetch_result.text or "").lower()

    if "/login" in final_url or "checkpoint" in final_url:
        return False
    if extract_username_from_url(fetch_result.final_url).lower() == slug:
        return True
    if f"/{slug}" in final_url:
        return True
    return slug in body
