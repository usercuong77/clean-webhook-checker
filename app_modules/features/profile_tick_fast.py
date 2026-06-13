from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, urlparse, urlunparse

import requests

from app_modules.features.facebook_profile_fetch import FetchResult, cookie_headers, fetch_limited_text, public_headers
from app_modules.features.profile_tick_parser import (
    VERIFIED_ACCOUNT_LABEL,
    VERIFIED_MARKER_BYTES,
    auth_wall_reason,
    login_next_target,
    profile_seen,
    unavailable_reason,
    verified_label,
)
from app_modules.resolvers.facebook_cookies import load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import extract_uid_from_url, normalize_uid, normalize_url_input


@dataclass(frozen=True)
class FastProfileTickResult:
    uid: str = ""
    username: str = ""
    canonical_url: str = ""
    verified_label: str = ""
    source: str = "profile_tick_lite_fast"
    reason: str = "not_checked"
    http_code: int = 0
    probes: list[dict[str, Any]] = field(default_factory=list)
    used_cookie: bool = False


def resolve_profile_verified_lite_fast(raw_input: str, force_cookie: bool = False) -> FastProfileTickResult:
    started = time.perf_counter()
    target = normalize_tick_input(raw_input)
    uid = uid_from_tick_input(raw_input, target)
    username = username_from_url(target)
    canonical_url = canonical_tick_url(target, uid)
    probes: list[dict[str, Any]] = []

    if not force_cookie:
        public = _run_public_fast(target, uid, username, canonical_url, probes)
        if public.verified_label:
            return public
        if unavailable_reason_from_result(public):
            return public
        if not should_cookie_fallback(public, target, uid, username):
            return public

        next_target = login_next_target_from_probes(probes)
        if next_target:
            target = normalize_tick_input(next_target)
            uid = uid_from_tick_input(next_target, target) or uid
            username = username_from_url(target) or username
            canonical_url = canonical_tick_url(target, uid)

    cookie = _run_cookie_fast(
        target=target,
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        probes=probes,
        force_cookie=force_cookie,
        started=started,
    )
    if cookie.used_cookie or force_cookie:
        return cookie
    return FastProfileTickResult(
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_lite_fast",
        reason="lite_fast_verified_not_found",
        http_code=last_probe_http_code(probes),
        probes=probes,
        used_cookie=False,
    )


def normalize_tick_input(raw_input: str) -> str:
    value = str(raw_input or "").strip().strip("#")
    value = "".join(ch for ch in value if ch not in "\u200b\u200c\u200d\ufeff")
    if not value:
        return ""
    uid = normalize_uid(value)
    if uid:
        return f"https://www.facebook.com/profile.php?id={uid}"
    if not value.startswith(("http://", "https://")):
        return f"https://www.facebook.com/{quote(value.strip('/'), safe='.')}"
    normalized = normalize_url_input(value) or value
    parsed = urlparse(normalized)
    if parsed.netloc and parsed.netloc.lower() != "www.facebook.com":
        parsed = parsed._replace(netloc="www.facebook.com")
        normalized = urlunparse(parsed)
    return normalized.rstrip("#")


def username_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    path = (parsed.path or "").strip("/")
    if not path or path.lower() in {"profile.php", "login"}:
        return ""
    first = path.split("/")[0].strip()
    if not first or first.isdigit() or first.lower() in {"share", "people", "photo.php", "permalink.php"}:
        return ""
    return first


def uid_from_tick_input(raw_input: str, normalized: str) -> str:
    direct = normalize_uid(str(raw_input or "")) or extract_uid_from_url(str(normalized or ""))
    if direct:
        return direct
    raw_value = str(raw_input or "").strip().strip("#")
    if raw_value.isdigit():
        return raw_value
    parsed = urlparse(str(normalized or ""))
    query_uid = (parse_qs(parsed.query).get("id") or [""])[0].strip()
    if query_uid.isdigit():
        return query_uid
    first_path = (parsed.path or "").strip("/").split("/")[0].strip()
    if first_path.isdigit():
        return first_path
    return ""


def canonical_tick_url(normalized: str, uid: str) -> str:
    return f"https://www.facebook.com/profile.php?id={uid}" if uid else str(normalized or "").strip()


def _run_public_fast(
    target: str,
    uid: str,
    username: str,
    canonical_url: str,
    probes: list[dict[str, Any]],
) -> FastProfileTickResult:
    timeout = public_timeout()
    for url in public_candidate_urls(target, uid, username)[:public_max_probes()]:
        fetch = fetch_limited_text(
            url,
            public_headers(),
            timeout=timeout,
            max_bytes=public_read_cap_bytes(),
            stop_markers=VERIFIED_MARKER_BYTES,
        )
        label = verified_label(fetch.text)
        reason = public_reason(label, fetch, uid, username)
        probes.append(probe_record("profile_tick_lite_fast_no_cookie", url, fetch, reason, label, False))
        result = FastProfileTickResult(
            uid=uid or extract_uid_from_url(fetch.final_url),
            username=username or username_from_url(fetch.final_url),
            canonical_url=canonical_tick_url(fetch.final_url or canonical_url, uid or extract_uid_from_url(fetch.final_url)),
            verified_label=label,
            source="profile_tick_lite_fast_no_cookie",
            reason=reason,
            http_code=fetch.http_code,
            probes=probes,
            used_cookie=False,
        )
        if label or unavailable_reason_from_result(result) or should_cookie_fallback(result, target, uid, username):
            return result
    return FastProfileTickResult(
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_lite_fast_no_cookie",
        reason="lite_fast_no_cookie_verified_not_found",
        http_code=last_probe_http_code(probes),
        probes=probes,
        used_cookie=False,
    )


def _run_cookie_fast(
    *,
    target: str,
    uid: str,
    username: str,
    canonical_url: str,
    probes: list[dict[str, Any]],
    force_cookie: bool,
    started: float,
) -> FastProfileTickResult:
    accounts = first_cookie_accounts(force_cookie)
    if not accounts:
        return FastProfileTickResult(
            uid=uid,
            username=username,
            canonical_url=canonical_url,
            source="profile_tick_lite_fast_cookie",
            reason="cookie_no_live_account_available",
            http_code=last_probe_http_code(probes),
            probes=probes,
            used_cookie=False,
        )

    total_deadline = cookie_total_deadline(force_cookie)
    for account in accounts:
        if not getattr(account, "is_usable", False):
            continue
        with requests.Session() as session:
            for url in cookie_candidate_urls(target, uid, username)[:cookie_max_probes(force_cookie)]:
                if time.perf_counter() - started >= total_deadline:
                    return FastProfileTickResult(
                        uid=uid,
                        username=username,
                        canonical_url=canonical_url,
                        source="profile_tick_lite_fast_cookie",
                        reason="cookie_fast_deadline_verified_not_found",
                        http_code=last_probe_http_code(probes),
                        probes=probes,
                        used_cookie=True,
                    )
                fetch = fetch_limited_text(
                    url,
                    cookie_headers(account),
                    timeout=cookie_timeout(force_cookie),
                    max_bytes=cookie_read_cap_bytes(uid),
                    stop_markers=VERIFIED_MARKER_BYTES,
                    session=session,
                )
                label = verified_label(fetch.text)
                reason = cookie_reason(label, fetch, uid, username)
                probes.append(
                    probe_record(
                        "profile_tick_lite_fast_cookie",
                        url,
                        fetch,
                        reason,
                        label,
                        True,
                        cookie_account=str(getattr(account, "masked_id", "") or ""),
                    )
                )
                if label:
                    return FastProfileTickResult(
                        uid=uid or extract_uid_from_url(fetch.final_url),
                        username=username or username_from_url(fetch.final_url),
                        canonical_url=canonical_tick_url(fetch.final_url or canonical_url, uid or extract_uid_from_url(fetch.final_url)),
                        verified_label=label,
                        source="profile_tick_lite_fast_cookie",
                        reason=reason,
                        http_code=fetch.http_code,
                        probes=probes,
                        used_cookie=True,
                    )
                next_target = login_next_target(fetch.final_url)
                if next_target:
                    retry = _cookie_retry_login_next(
                        session=session,
                        account=account,
                        next_target=next_target,
                        uid=uid,
                        username=username,
                        canonical_url=canonical_url,
                        probes=probes,
                        force_cookie=force_cookie,
                    )
                    if retry.verified_label:
                        return retry
        if not force_cookie:
            break

    return FastProfileTickResult(
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_lite_fast_cookie",
        reason="cookie_fast_verified_not_found" if force_cookie else "no_cookie_and_cookie_fast_verified_not_found",
        http_code=last_probe_http_code(probes),
        probes=probes,
        used_cookie=True,
    )


def public_candidate_urls(target: str, uid: str, username: str) -> list[str]:
    urls: list[str] = []
    if uid:
        urls.extend(
            [
                f"https://www.facebook.com/profile.php?id={uid}&sk=about",
                f"https://www.facebook.com/profile.php?id={uid}",
            ]
        )
    elif username:
        safe = quote(username, safe=".")
        urls.extend([f"https://www.facebook.com/{safe}/about", f"https://www.facebook.com/{safe}"])
    if target:
        urls.append(target)
        about = about_url(target)
        if about:
            urls.insert(0, about)
    return unique_urls(urls)


def cookie_candidate_urls(target: str, uid: str, username: str) -> list[str]:
    urls: list[str] = []
    if uid:
        urls.extend([target, f"https://www.facebook.com/profile.php?id={uid}", f"https://www.facebook.com/profile.php?id={uid}&sk=about"])
    elif username:
        safe = quote(username, safe=".")
        urls.extend([f"https://www.facebook.com/{safe}", f"https://www.facebook.com/{safe}/about"])
    if target:
        urls.append(target)
        about = about_url(target)
        if about:
            urls.append(about)
    return unique_urls(urls)


def _cookie_retry_login_next(
    *,
    session: requests.Session,
    account: Any,
    next_target: str,
    uid: str,
    username: str,
    canonical_url: str,
    probes: list[dict[str, Any]],
    force_cookie: bool,
) -> FastProfileTickResult:
    retry_urls = unique_urls([normalize_tick_input(next_target), about_url(normalize_tick_input(next_target))])
    for retry_url in retry_urls[:2]:
        fetch = fetch_limited_text(
            retry_url,
            cookie_headers(account),
            timeout=cookie_timeout(force_cookie),
            max_bytes=cookie_read_cap_bytes(uid),
            stop_markers=VERIFIED_MARKER_BYTES,
            session=session,
        )
        retry_uid = uid or extract_uid_from_url(fetch.final_url)
        retry_username = username or username_from_url(fetch.final_url) or username_from_url(retry_url)
        label = verified_label(fetch.text)
        reason = "cookie_fast_login_next_verified_found" if label else cookie_reason(label, fetch, retry_uid, retry_username)
        probes.append(
            probe_record(
                "profile_tick_lite_fast_cookie",
                retry_url,
                fetch,
                reason,
                label,
                True,
                cookie_account=str(getattr(account, "masked_id", "") or ""),
            )
        )
        if label:
            return FastProfileTickResult(
                uid=retry_uid,
                username=retry_username,
                canonical_url=canonical_tick_url(fetch.final_url or canonical_url, retry_uid),
                verified_label=label,
                source="profile_tick_lite_fast_cookie",
                reason=reason,
                http_code=fetch.http_code,
                probes=probes,
                used_cookie=True,
            )
    return FastProfileTickResult(
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_lite_fast_cookie",
        reason="cookie_fast_login_next_verified_not_found",
        http_code=last_probe_http_code(probes),
        probes=probes,
        used_cookie=True,
    )


def about_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.netloc:
        return ""
    path = (parsed.path or "/").rstrip("/")
    if path.endswith("/about") or path.lower() == "/profile.php":
        return url
    if path:
        return urlunparse(parsed._replace(path=f"{path}/about"))
    return ""


def should_cookie_fallback(result: FastProfileTickResult, target: str, uid: str, username: str) -> bool:
    _ = target
    _ = uid
    _ = username
    return not bool(result.verified_label)


def public_reason(label: str, fetch: FetchResult, uid: str, username: str) -> str:
    if label:
        return "lite_fast_no_cookie_verified_found"
    unavailable = unavailable_reason(fetch.text, fetch.http_code)
    if unavailable:
        return f"lite_fast_no_cookie_{unavailable}"
    auth = auth_wall_reason(fetch.text, fetch.final_url)
    if auth:
        return f"lite_fast_no_cookie_{auth}"
    if profile_seen(fetch.text, fetch.final_url, uid, username):
        return "lite_fast_no_cookie_profile_seen_not_verified"
    return f"lite_fast_no_cookie_{fetch.reason or 'verified_not_found'}"


def cookie_reason(label: str, fetch: FetchResult, uid: str, username: str) -> str:
    if label:
        return "cookie_fast_verified_found"
    unavailable = unavailable_reason(fetch.text, fetch.http_code)
    if unavailable:
        return f"cookie_fast_{unavailable}"
    auth = auth_wall_reason(fetch.text, fetch.final_url)
    if auth:
        return f"cookie_fast_{auth}"
    if profile_seen(fetch.text, fetch.final_url, uid, username):
        return "cookie_fast_profile_seen_not_verified"
    return f"cookie_fast_{fetch.reason or 'verified_not_found'}"


def unavailable_reason_from_result(result: FastProfileTickResult) -> bool:
    return any(marker in str(result.reason or "").lower() for marker in ("profile_unavailable", "http_404"))


def login_next_target_from_probes(probes: list[dict[str, Any]]) -> str:
    for probe in reversed(probes):
        target = login_next_target(str(probe.get("finalUrl") or ""))
        if target:
            return target
    return ""


def first_cookie_accounts(force_cookie: bool):
    limit = cookie_account_limit(force_cookie)
    return [account for account in load_cookie_accounts()[:limit] if getattr(account, "is_usable", False)]


def cookie_account_limit(force_cookie: bool) -> int:
    key = "PROFILE_TICK_FAST_FORCE_COOKIE_ACCOUNT_LIMIT" if force_cookie else "PROFILE_TICK_FAST_COOKIE_ACCOUNT_LIMIT"
    default = 2 if force_cookie else 1
    try:
        return max(0, min(int(os.getenv(key, str(default))), 4))
    except ValueError:
        return default


def public_timeout() -> float:
    try:
        return max(0.5, min(float(os.getenv("PROFILE_TICK_FAST_PUBLIC_TIMEOUT_SEC", "1.4")), 2.5))
    except ValueError:
        return 1.4


def cookie_timeout(force_cookie: bool) -> float:
    default = 2.2 if force_cookie else 1.8
    try:
        return max(0.8, min(float(os.getenv("PROFILE_TICK_FAST_COOKIE_TIMEOUT_SEC", str(default))), 3.0))
    except ValueError:
        return default


def cookie_total_deadline(force_cookie: bool) -> float:
    default = 4.8 if force_cookie else 3.2
    try:
        return max(1.2, min(float(os.getenv("PROFILE_TICK_FAST_COOKIE_TOTAL_DEADLINE_SEC", str(default))), 6.0))
    except ValueError:
        return default


def public_read_cap_bytes() -> int:
    try:
        return max(120_000, min(int(os.getenv("PROFILE_TICK_FAST_PUBLIC_READ_CAP_BYTES", "420000")), 900_000))
    except ValueError:
        return 420_000


def cookie_read_cap_bytes(uid: str) -> int:
    default = 850_000 if uid else 650_000
    try:
        return max(180_000, min(int(os.getenv("PROFILE_TICK_FAST_COOKIE_READ_CAP_BYTES", str(default))), 1_400_000))
    except ValueError:
        return default


def public_max_probes() -> int:
    try:
        return max(1, min(int(os.getenv("PROFILE_TICK_FAST_PUBLIC_MAX_PROBES", "1")), 3))
    except ValueError:
        return 1


def cookie_max_probes(force_cookie: bool) -> int:
    default = 2
    try:
        return max(1, min(int(os.getenv("PROFILE_TICK_FAST_COOKIE_MAX_PROBES", str(default))), 4))
    except ValueError:
        return default


def probe_record(
    source: str,
    url: str,
    fetch: FetchResult,
    reason: str,
    label: str,
    used_cookie: bool,
    cookie_account: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "source": source,
        "url": url,
        "httpCode": fetch.http_code,
        "finalUrl": fetch.final_url,
        "reason": reason,
        "hasName": False,
        "elapsedMs": fetch.elapsed_ms,
        "usedCookie": used_cookie,
    }
    if label:
        item["verifiedLabel"] = label
    if cookie_account:
        item["cookieAccount"] = cookie_account
    return item


def last_probe_http_code(probes: list[dict[str, Any]]) -> int:
    for probe in reversed(probes):
        try:
            return int(probe.get("httpCode") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def unique_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        value = str(url or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
