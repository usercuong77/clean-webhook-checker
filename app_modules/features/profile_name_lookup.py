"""Fast no-cache profile-name lookup for /check and /add.

Checktick keeps its deeper verified-account workflow in profile_name.py.  This
module is intentionally name-only: short timeouts, limited HTML reads, cookie
from the bot pool only, and no fallback that turns a UID into a display name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from app_modules.checkers.live_die import LiveDieResult
from app_modules.features.profile_name import (
    ProfileNameResult,
    build_profile_name_urls as legacy_build_profile_name_urls,
    clean_profile_name_candidate,
    extract_profile_name,
    is_valid_profile_name,
)
from app_modules.features.profile_name import (
    _cookie_desktop_headers,
    _cookie_mobile_headers,
    _fetch_limited_text,
    _login_next_profile_target,
    _profile_about_url,
    _profile_tick_username_from_url,
    _unique,
)
from app_modules.resolvers.facebook_cookies import load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import extract_uid_from_url
from app_modules.resolvers.uid_resolver import ResolvedInput


DEFAULT_NAME_TIMEOUT_SEC = 2.2
DEFAULT_NAME_READ_CAP_BYTES = 220_000
DEFAULT_NAME_MAX_PROBES = 8


@dataclass
class _NameProbe:
    source: str
    url: str
    final_url: str
    http_code: int
    name: str
    reason: str
    elapsed_ms: int
    header_label: str = ""
    login_next_target: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "source": self.source,
            "url": self.url,
            "finalUrl": self.final_url,
            "httpCode": self.http_code,
            "name": self.name,
            "reason": self.reason,
            "elapsedMs": self.elapsed_ms,
        }
        if self.header_label:
            data["headerLabel"] = self.header_label
        if self.login_next_target:
            data["loginNextTarget"] = self.login_next_target
        return data


def choose_profile_name(
    resolved: ResolvedInput,
    live_die: LiveDieResult,
    include_name: bool = True,
) -> str:
    if not include_name:
        return ""
    if live_die.status == "DIE":
        return ""
    if live_die.status != "LIVE":
        return _safe_resolver_name(resolved)

    resolver_name = _safe_resolver_name(resolved)
    if resolver_name:
        return resolver_name

    result = resolve_profile_name(resolved)
    return result.name


def resolve_profile_name(resolved: ResolvedInput, include_verified: bool = False) -> ProfileNameResult:
    del include_verified
    urls = build_profile_name_urls(resolved)
    if not urls:
        return ProfileNameResult("", "profile_name_fast", "no_profile_urls")

    accounts = [account for account in load_cookie_accounts()[:_cookie_account_limit()] if account.is_usable]
    if not accounts:
        return ProfileNameResult("", "profile_name_fast", "no_cookie_accounts")

    timeout = _name_timeout()
    max_bytes = _name_read_cap_bytes()
    max_probes = _name_max_probes()
    probes: list[_NameProbe] = []
    seen_retry: set[str] = set()
    probe_count = 0

    for account in accounts:
        for url, headers, label in _fast_cookie_candidates(urls, account):
            if probe_count >= max_probes:
                return _miss("name_not_found_probe_limit", probes)
            probe_count += 1
            result = _fetch_name_candidate(url, headers, label, timeout, max_bytes)
            probes.append(result)
            if result.name:
                return ProfileNameResult(result.name, "profile_name_fast_cookie", result.reason, _probe_dicts(probes))

            target = result.login_next_target or _login_next_profile_target(result.final_url)
            if not target:
                target = _redirect_profile_target(result.final_url, result.url)
            if not target:
                continue
            for retry_url in _retry_urls_from_login_target(target):
                retry_key = f"{label}|{retry_url.lower()}"
                if retry_key in seen_retry:
                    continue
                seen_retry.add(retry_key)
                if probe_count >= max_probes:
                    return _miss("name_not_found_probe_limit", probes)
                probe_count += 1
                retry = _fetch_name_candidate(
                    retry_url,
                    headers,
                    f"{label}_login_next",
                    timeout,
                    max_bytes,
                    login_next_target=target,
                )
                probes.append(retry)
                if retry.name:
                    return ProfileNameResult(
                        retry.name,
                        "profile_name_fast_cookie",
                        "name_found_login_next",
                        _probe_dicts(probes),
                    )

    return _miss("name_not_found", probes)


def build_profile_name_urls(resolved: ResolvedInput) -> list[str]:
    uid = str(resolved.uid or "").strip()
    username = _clean_username(str(resolved.username or ""))
    canonical = str(resolved.canonical_url or "").strip()
    urls: list[str] = []

    if username:
        safe = quote(username, safe=".")
        urls.extend(
            [
                f"https://www.facebook.com/{safe}/about",
                f"https://www.facebook.com/{safe}",
            ]
        )

    if uid:
        urls.extend(
            [
                f"https://www.facebook.com/profile.php?id={uid}",
                f"https://www.facebook.com/profile.php?id={uid}&sk=about",
            ]
        )

    if canonical:
        urls.append(canonical)
        about = _profile_about_url(canonical)
        if about:
            urls.append(about)

    if not urls:
        urls.extend(legacy_build_profile_name_urls(resolved))

    return _unique([url for url in urls if url])


def clear_profile_name_cache() -> None:
    return None


def _fetch_name_candidate(
    url: str,
    headers: Mapping[str, str],
    header_label: str,
    timeout: float,
    max_bytes: int,
    login_next_target: str = "",
) -> _NameProbe:
    started = perf_counter()
    fetch = _fetch_limited_text(url, headers, timeout, max_bytes)
    elapsed_ms = int((perf_counter() - started) * 1000)
    name = clean_profile_name_candidate(extract_profile_name(fetch.text))
    if not is_valid_profile_name(name):
        name = ""
    target = login_next_target or _login_next_profile_target(fetch.final_url)
    reason = "name_found" if name else fetch.reason or "name_not_found"
    if target and not name:
        reason = "login_next_without_name"
    return _NameProbe(
        source="profile_name_fast_cookie",
        url=url,
        final_url=fetch.final_url,
        http_code=fetch.http_code,
        name=name,
        reason=reason,
        elapsed_ms=elapsed_ms,
        header_label=header_label,
        login_next_target=target,
    )


def _fast_cookie_candidates(urls: list[str], account) -> list[tuple[str, dict[str, str], str]]:
    desktop_headers = _cookie_desktop_headers(account)
    mobile_headers = _cookie_mobile_headers(account)
    www_urls = [url for url in urls if "www.facebook.com" in url.lower()]
    mobile_urls = _mobile_variants(www_urls or urls)
    rounds = [
        ("desktop_logged_in", www_urls, desktop_headers),
        ("mobile_logged_in", mobile_urls, mobile_headers),
    ]

    out: list[tuple[str, dict[str, str], str]] = []
    seen: set[str] = set()
    for label, round_urls, headers in rounds:
        for url in round_urls:
            key = f"{label}|{url}"
            if key in seen:
                continue
            seen.add(key)
            out.append((url, dict(headers), label))
    return out


def _retry_urls_from_login_target(target: str) -> list[str]:
    value = str(target or "").strip()
    if not value:
        return []
    username = _profile_tick_username_from_url(value)
    uid = extract_uid_from_url(value)
    urls = [value]
    if username:
        safe = quote(username, safe=".")
        urls.extend([f"https://www.facebook.com/{safe}/about", f"https://www.facebook.com/{safe}"])
    elif uid:
        urls.extend([f"https://www.facebook.com/profile.php?id={uid}", f"https://www.facebook.com/profile.php?id={uid}&sk=about"])
    about = _profile_about_url(value)
    if about:
        urls.insert(0, about)
    return _unique(urls)[:3]


def _redirect_profile_target(final_url: str, original_url: str) -> str:
    final_value = str(final_url or "").strip()
    original_value = str(original_url or "").strip()
    if not final_value or final_value.rstrip("/").lower() == original_value.rstrip("/").lower():
        return ""
    parsed = urlparse(final_value)
    if not parsed.netloc.lower().endswith("facebook.com"):
        return ""
    path = (parsed.path or "/").strip("/")
    if not path or path.lower().startswith(("login", "share")):
        return ""
    username = _profile_tick_username_from_url(final_value)
    if username or extract_uid_from_url(final_value):
        return final_value
    return ""


def _mobile_variants(urls: list[str]) -> list[str]:
    out: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if not parsed.netloc:
            continue
        host = parsed.netloc.lower()
        if not host.endswith("facebook.com"):
            continue
        if host == "m.facebook.com":
            out.append(url)
            continue
        out.append(url.replace(parsed.netloc, "m.facebook.com", 1))
    return _unique(out)


def _clean_username(value: str) -> str:
    username = value.strip().strip("/")
    if not username:
        return ""
    if username.lower() in {"share", "profile.php", "people", "login"}:
        return ""
    if username.isdigit():
        return ""
    return username


def _safe_resolver_name(resolved: ResolvedInput) -> str:
    name = clean_profile_name_candidate(str(getattr(resolved, "resolver_name", "") or ""))
    if not is_valid_profile_name(name):
        return ""
    uid = str(resolved.uid or "").strip()
    if uid and name == uid:
        return ""
    return name


def _miss(reason: str, probes: list[_NameProbe]) -> ProfileNameResult:
    return ProfileNameResult("", "profile_name_fast_cookie", reason, _probe_dicts(probes))


def _probe_dicts(probes: list[_NameProbe]) -> list[dict[str, Any]]:
    return [probe.to_dict() for probe in probes]


def _name_timeout() -> float:
    try:
        return max(1.0, min(float(os.getenv("PROFILE_NAME_FAST_TIMEOUT_SEC", str(DEFAULT_NAME_TIMEOUT_SEC))), 4.0))
    except ValueError:
        return DEFAULT_NAME_TIMEOUT_SEC


def _name_read_cap_bytes() -> int:
    try:
        return max(64_000, min(int(os.getenv("PROFILE_NAME_FAST_READ_CAP_BYTES", str(DEFAULT_NAME_READ_CAP_BYTES))), 600_000))
    except ValueError:
        return DEFAULT_NAME_READ_CAP_BYTES


def _name_max_probes() -> int:
    try:
        return max(2, min(int(os.getenv("PROFILE_NAME_FAST_MAX_PROBES", str(DEFAULT_NAME_MAX_PROBES))), 16))
    except ValueError:
        return DEFAULT_NAME_MAX_PROBES


def _cookie_account_limit() -> int:
    try:
        return max(0, min(int(os.getenv("PROFILE_NAME_FAST_COOKIE_ACCOUNT_LIMIT", "1")), 3))
    except ValueError:
        return 1


__all__ = [
    "ProfileNameResult",
    "build_profile_name_urls",
    "choose_profile_name",
    "clear_profile_name_cache",
    "extract_profile_name",
    "is_valid_profile_name",
    "resolve_profile_name",
]
