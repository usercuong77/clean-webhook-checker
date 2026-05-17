from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import requests

from app_modules.core.config import get_config
from app_modules.resolvers.facebook_cookies import cookie_header, load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import (
    build_facebook_probe_urls,
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

    timeout = max(5.0, get_config().request_timeout_seconds)
    probes: list[dict[str, Any]] = []

    for account in accounts:
        for probe_url in probe_urls:
            for headers in _cookie_header_candidates(account):
                fetch_result = _fetch_text_with_cookie(probe_url, headers, timeout)
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
                }

                uid_from_html = _extract_uid_from_cookie_html(fetch_result.text, account)
                if uid_from_html:
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

    return CookieUidResolution(
        "",
        "uid_cookie_resolver",
        "uid_not_found_after_cookie_probe",
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
    return [
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Cookie": cookie_header(account),
        }
        for user_agent in COOKIE_UID_USER_AGENTS
    ]


def _extract_uid_from_cookie_html(text: str, account) -> str:
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
