from __future__ import annotations

import html as html_lib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse

import requests

from app_modules.checkers.live_die import LiveDieResult
from app_modules.core.config import get_config
from app_modules.resolvers.facebook_cookies import cookie_header, load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import (
    extract_uid_from_url,
    extract_username_from_url,
    normalize_uid,
    normalize_url_input,
)
from app_modules.resolvers.uid_resolver import ResolvedInput


PROFILE_NAME_BLOCKLIST = [
    "facebook",
    "log in",
    "login",
    "sign up",
    "log in or sign up to view",
    "dang nhap",
    "dang nh?p",
    "dang nhap hoac dang ky de xem",
    "tao tai khoan",
    "t?o tai kho?n",
    "dang ky de xem",
    "đăng nhập",
    "đăng ký",
    "đăng nhập hoặc đăng ký để xem",
    "ÄÄng nháº­p",
    "ÄÄng kÃ½",
    "ÄÄng nháº­p hoáº·c ÄÄng kÃ½ Äá» xem",
    "create new account",
    "forgot password",
    "quen mat khau",
    "quên mật khẩu",
    "meta",
    "trình duyệt này không hỗ trợ",
    "trình duyệt này không được hỗ trợ",
    "không được hỗ trợ",
    "unsupported browser",
    "this browser isn't supported",
    "browser isn't supported",
    "content isn't available",
    "error",
    "loi",
    "lỗi",
    "lá»i",
    "page not found",
    "this page isn't available",
    "sorry, something went wrong",
    "temporarily unavailable",
    "security check",
    "checkpoint",
    "tin nhắn",
    "messenger",
    "messages",
    "message requests",
    "thông báo",
    "notifications",
    "friend requests",
    "lời mời kết bạn",
    "bảng feed",
    "news feed",
    "trang chủ",
    "home",
]

LETTER_RE = re.compile(r"[A-Za-zÀ-ỹ]")
TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HEADING_RE = re.compile(r"<(h1|strong)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r"""([A-Za-z_:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)

VERIFIED_ACCOUNT_LABEL = "T\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh"
VERIFIED_MARKER_PATTERNS = [
    re.compile(r'"show_verified_badge_on_profile"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"is_verified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"isVerified"\s*:\s*true', re.IGNORECASE),
]
PROFILE_HEADER_CONTEXT_MARKERS = (
    "profile_header_renderer",
    "profilecometheader",
    "xfbprofileentityconvergenceheaderrenderer",
    "profile_header",
    "cover_photo",
    "profile_intro_card",
    "profile_tile_section",
    "cometprofileplus",
    "profile_owner",
    "owning_profile",
    "profile_owner_id",
    "timelineprofile",
)
COMMENT_CONTEXT_MARKERS = (
    "cometuficomment",
    "cometcommentnameandbadges",
    "comet_comment_author_name",
    "comment_author",
    '"comment"',
    "comment_id",
    "feedback_comment",
    "comment_list",
)
TICK_PUBLIC_READ_CAP_BYTES = 1_800_000
TICK_COOKIE_READ_CAP_BYTES = 2_800_000
PROFILE_TICK_VERIFIED_MARKERS = (
    "verified account",
    "tài khoản đã xác minh",
    "tai khoan da xac minh",
    '"show_verified_badge_on_profile":true',
    '"is_verified":true',
    '"isVerified":true',
    'show_verified_badge_on_profile\\":true',
    'is_verified\\":true',
    'isVerified\\":true',
)
BUILTIN_CONFIRMED_PROFILE_NAME_MAP = {
    "100080441816993": "Ng Trinh",
    "100010211341364": "Vo Khac Duy",
    "100041007767995": "caubeoooo",
    "100042281496124": "PHAM TAN KIET",
    "61560438496711": "Nghia Le",
    "100004507923562": "Pkdt Trang Nguyen",
    "100002614628083": "Thanh Cuong",
    "9209278": "Nguyen Minh Huy",
}
@dataclass(frozen=True)
class ProfileNameResult:
    name: str
    source: str
    reason: str
    probes: list[dict[str, Any]] = field(default_factory=list)
    verified_label: str = ""


@dataclass(frozen=True)
class ProfileTickResult:
    name: str
    display_name: str
    verified_label: str
    uid: str
    username: str
    canonical_url: str
    source: str
    reason: str
    http_code: int
    probes: list[dict[str, Any]] = field(default_factory=list)
    used_cookie: bool = False

@dataclass(frozen=True)
class FetchResult:
    http_code: int
    text: str
    final_url: str
    reason: str


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
        return resolved.username

    result = resolve_profile_name(resolved)
    if result.name:
        return result.name
    return resolved.username or resolved.uid


def resolve_profile_name(resolved: ResolvedInput, include_verified: bool = False) -> ProfileNameResult:
    uid = str(resolved.uid or "").strip()
    known_name = ""
    if uid:
        known_name = _known_profile_name(uid)
        if known_name and not include_verified:
            return ProfileNameResult(known_name, "profile_name_known_map", "name_found_known_map")

    urls = build_profile_name_urls(resolved)
    if not urls:
        resolver_name = str(getattr(resolved, "resolver_name", "") or "").strip()
        if resolver_name and is_valid_profile_name(resolver_name):
            return ProfileNameResult(resolver_name, "resolver_name", "name_found_resolver")
        return ProfileNameResult("", "profile_name", "no_profile_urls")

    timeout = max(4.0, min(get_config().request_timeout_seconds, 8.0))
    probes: list[dict[str, Any]] = []

    cookie_limit = _cookie_account_limit()
    for account in load_cookie_accounts()[:cookie_limit]:
        if not account.is_usable:
            continue
        for url, headers, header_label in _cookie_probe_candidates(urls, account):
            fetch = _fetch_text(url, headers, timeout)
            if uid and uid not in fetch.text and uid not in fetch.final_url:
                probes.append(
                    _probe_record(
                        "profile_name_cookie",
                        url,
                        fetch,
                        "",
                        account.masked_id,
                        "target_uid_not_in_cookie_html",
                        header_label,
                    )
                )
                continue
            name = extract_profile_name(fetch.text)
            verified_label = extract_profile_verified_label(fetch.text, name)
            probe = _probe_record(
                "profile_name_cookie",
                url,
                fetch,
                name,
                account.masked_id,
                header_label=header_label,
                verified_label=verified_label,
            )
            probes.append(probe)
            if name:
                return ProfileNameResult(
                    name,
                    "profile_name_cookie",
                    "name_found_cookie",
                    probes,
                    verified_label=verified_label,
                )

    if known_name:
        return ProfileNameResult(known_name, "profile_name_known_map", "name_found_known_map", probes)

    resolver_name = str(getattr(resolved, "resolver_name", "") or "").strip()
    if resolver_name and is_valid_profile_name(resolver_name):
        return ProfileNameResult(resolver_name, "resolver_name", "name_found_resolver", probes)

    return ProfileNameResult("", "profile_name", "name_not_found", probes)


def resolve_profile_tick_from_input(raw_input: str, force_cookie: bool = False) -> ProfileTickResult:
    value = str(raw_input or "").strip()
    normalized = normalize_url_input(value)
    uid = normalize_uid(value) or extract_uid_from_url(normalized)
    username = extract_username_from_url(normalized)
    canonical_url = _canonical_profile_tick_url(normalized, uid)
    probes: list[dict[str, Any]] = []
    timeout = max(4.0, min(get_config().request_timeout_seconds, 8.0))

    if not force_cookie:
        public = _resolve_profile_tick_no_cookie(
            normalized=normalized,
            uid=uid,
            username=username,
            canonical_url=canonical_url,
            timeout=timeout,
            probes=probes,
        )
        if public.name or public.verified_label:
            return public
        unwrapped = _first_login_next_target_from_probes(probes)
        if unwrapped:
            normalized = unwrapped
            uid = extract_uid_from_url(normalized) or uid
            username = extract_username_from_url(normalized) or username
            canonical_url = _canonical_profile_tick_url(normalized, uid)

    cookie = _resolve_profile_tick_with_cookie(
        normalized=normalized,
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        timeout=timeout,
        probes=probes,
        forced=force_cookie,
    )
    if cookie.name or cookie.verified_label or force_cookie:
        return cookie
    if cookie.used_cookie:
        return cookie

    return ProfileTickResult(
        name="",
        display_name="",
        verified_label="",
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick",
        reason="name_and_verified_not_found",
        http_code=_last_probe_http_code(probes),
        probes=probes,
        used_cookie=False,
    )


def _resolve_profile_tick_no_cookie(
    normalized: str,
    uid: str,
    username: str,
    canonical_url: str,
    timeout: float,
    probes: list[dict[str, Any]],
) -> ProfileTickResult:
    best_name_result: ProfileTickResult | None = None
    seen_unwrapped: set[str] = set()
    for url, headers, header_label in _public_tick_probe_candidates(normalized, uid, username):
        results = _profile_tick_results_from_candidate(
            url=url,
            headers=headers,
            timeout=timeout,
            max_bytes=TICK_PUBLIC_READ_CAP_BYTES,
            raw_uid=uid,
            raw_username=username,
            fallback_canonical_url=canonical_url,
            source="profile_tick_no_cookie",
            reason_prefix="no_cookie",
            header_label=header_label,
            used_cookie=False,
            probes=probes,
            seen_unwrapped=seen_unwrapped,
        )
        for result in results:
            if result.verified_label:
                return result
            if result.name and best_name_result is None:
                best_name_result = result

    if best_name_result:
        return best_name_result

    return ProfileTickResult(
        name="",
        display_name="",
        verified_label="",
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_no_cookie",
        reason="no_cookie_name_and_verified_not_found",
        http_code=_last_probe_http_code(probes),
        probes=probes,
        used_cookie=False,
    )


def _resolve_profile_tick_with_cookie(
    normalized: str,
    uid: str,
    username: str,
    canonical_url: str,
    timeout: float,
    probes: list[dict[str, Any]],
    forced: bool,
) -> ProfileTickResult:
    best_name_result: ProfileTickResult | None = None
    seen_unwrapped: set[str] = set()
    for account in load_cookie_accounts()[:_tick_cookie_account_limit(forced)]:
        if not account.is_usable:
            continue
        account_name_result: ProfileTickResult | None = None
        for url, headers, header_label in _cookie_tick_probe_candidates(normalized, uid, username, account):
            results = _profile_tick_results_from_candidate(
                url=url,
                headers=headers,
                timeout=timeout,
                max_bytes=TICK_COOKIE_READ_CAP_BYTES,
                raw_uid=uid,
                raw_username=username,
                fallback_canonical_url=canonical_url,
                source="profile_tick_cookie",
                reason_prefix="cookie_forced" if forced else "cookie_fallback",
                header_label=header_label,
                used_cookie=True,
                probes=probes,
                cookie_account=account.masked_id,
                seen_unwrapped=seen_unwrapped,
            )
            for result in results:
                if result.verified_label:
                    return result
                if result.name and account_name_result is None:
                    account_name_result = result
        if account_name_result:
            if not forced:
                return account_name_result
            if best_name_result is None:
                best_name_result = account_name_result

    if best_name_result:
        return best_name_result

    return ProfileTickResult(
        name="",
        display_name="",
        verified_label="",
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source="profile_tick_cookie",
        reason="cookie_name_and_verified_not_found" if forced else "no_cookie_and_cookie_name_not_found",
        http_code=_last_probe_http_code(probes),
        probes=probes,
        used_cookie=True,
    )


def _public_tick_probe_candidates(normalized: str, uid: str, username: str) -> list[tuple[str, dict[str, str], str]]:
    urls = _fast_profile_tick_urls(normalized, uid, username)
    headers = _facebook_catalog_headers()
    out: list[tuple[str, dict[str, str], str]] = []
    seen: set[str] = set()
    for url in urls:
        key = f"facebookcatalog|{url}"
        if key in seen:
            continue
        seen.add(key)
        out.append((url, dict(headers), "facebookcatalog"))
    return out


def _cookie_tick_probe_candidates(normalized: str, uid: str, username: str, account) -> list[tuple[str, dict[str, str], str]]:
    urls = _fast_profile_tick_urls(normalized, uid, username)
    headers = _cookie_desktop_headers(account)
    return [(url, dict(headers), "desktop_logged_in") for url in urls]


def _fast_profile_tick_urls(normalized: str, uid: str, username: str) -> list[str]:
    return _profile_tick_urls(normalized, uid, username)[:2]


def _profile_tick_urls(normalized: str, uid: str, username: str) -> list[str]:
    urls: list[str] = []
    if normalized:
        urls.append(normalized)
        urls.append(_profile_about_url(normalized))
    if username:
        safe_username = quote(username.strip("/"), safe=".")
        urls.extend(
            [
                f"https://www.facebook.com/{safe_username}",
                f"https://www.facebook.com/{safe_username}/about",
            ]
        )
    if uid:
        urls.extend(
            [
                f"https://www.facebook.com/profile.php?id={uid}",
                f"https://www.facebook.com/profile.php?id={uid}&sk=about",
            ]
        )
    return _unique(urls)


def _profile_about_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if not value:
        return ""
    if "profile.php" in value:
        separator = "&" if "?" in value else "?"
        return f"{value}{separator}sk=about"
    if "/share/" in value.lower():
        return value
    return f"{value}/about"


def _profile_tick_result_from_fetch(
    fetch: FetchResult,
    raw_uid: str,
    raw_username: str,
    fallback_canonical_url: str,
    source: str,
    reason_prefix: str,
    header_label: str,
    used_cookie: bool,
    probes: list[dict[str, Any]],
    cookie_account: str = "",
) -> ProfileTickResult:
    name = extract_profile_name(fetch.text)
    verified_label = extract_profile_verified_label(fetch.text, name)
    display_name = _display_profile_name_value(name)
    uid = raw_uid or extract_uid_from_url(fetch.final_url)
    username = raw_username or extract_username_from_url(fetch.final_url)
    canonical_url = fetch.final_url or fallback_canonical_url
    reason = _profile_tick_reason(reason_prefix, name, verified_label, fetch.reason)
    probe = _probe_record(
        source,
        canonical_url,
        fetch,
        name,
        cookie_account=cookie_account,
        reason=reason,
        header_label=header_label,
        verified_label=verified_label,
    )
    probe["usedCookie"] = used_cookie
    probes.append(probe)

    return ProfileTickResult(
        name=display_name or name,
        display_name=display_name,
        verified_label=verified_label,
        uid=uid,
        username=username,
        canonical_url=canonical_url,
        source=source,
        reason=reason,
        http_code=fetch.http_code,
        probes=probes,
        used_cookie=used_cookie,
    )


def _profile_tick_results_from_candidate(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    raw_uid: str,
    raw_username: str,
    fallback_canonical_url: str,
    source: str,
    reason_prefix: str,
    header_label: str,
    used_cookie: bool,
    probes: list[dict[str, Any]],
    cookie_account: str = "",
    seen_unwrapped: set[str] | None = None,
) -> list[ProfileTickResult]:
    fetch = _fetch_limited_text(url, headers, timeout, max_bytes)
    results = [
        _profile_tick_result_from_fetch(
            fetch=fetch,
            raw_uid=raw_uid,
            raw_username=raw_username,
            fallback_canonical_url=fallback_canonical_url,
            source=source,
            reason_prefix=reason_prefix,
            header_label=header_label,
            used_cookie=used_cookie,
            probes=probes,
            cookie_account=cookie_account,
        )
    ]

    target = _login_next_profile_target(fetch.final_url)
    if not target:
        return results
    seen = seen_unwrapped if seen_unwrapped is not None else set()
    key = target.lower()
    if key in seen:
        return results
    seen.add(key)

    retry_fetch = _fetch_limited_text(target, headers, timeout, max_bytes)
    results.append(
        _profile_tick_result_from_fetch(
            fetch=retry_fetch,
            raw_uid=raw_uid or extract_uid_from_url(target),
            raw_username=raw_username or extract_username_from_url(target),
            fallback_canonical_url=target,
            source=source,
            reason_prefix=f"{reason_prefix}_login_next",
            header_label=header_label,
            used_cookie=used_cookie,
            probes=probes,
            cookie_account=cookie_account,
        )
    )
    probes[-1]["loginNextTarget"] = target
    return results


def _first_login_next_target_from_probes(probes: list[dict[str, Any]]) -> str:
    for probe in probes:
        target = str(probe.get("loginNextTarget") or "").strip()
        if target:
            return target
        for key in ("finalUrl", "url"):
            target = _login_next_profile_target(str(probe.get(key) or ""))
            if target:
                return target
    return ""


def _login_next_profile_target(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if not host.endswith("facebook.com"):
        return ""
    if not parsed.path.lower().startswith("/login"):
        return ""
    params = parse_qs(parsed.query)
    for raw_target in params.get("next", []):
        target = _clean_login_next_target(raw_target)
        if target:
            return target
    return ""


def _clean_login_next_target(raw_target: str) -> str:
    value = html_lib.unescape(unquote(str(raw_target or "").strip()))
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if not host.endswith("facebook.com"):
        return ""
    path = parsed.path or "/"
    lower_path = path.lower()
    if lower_path.startswith("/login") or lower_path.startswith("/share"):
        return ""
    if "profile.php" in lower_path:
        uid = extract_uid_from_url(value)
        if uid:
            return f"https://www.facebook.com/profile.php?id={uid}"
    clean_path = path.rstrip("/") or "/"
    if clean_path == "/":
        return ""
    return urlunparse(("https", "www.facebook.com", clean_path, "", "", ""))


def _known_profile_name(uid: str) -> str:
    uid_key = str(uid or "").strip()
    if not uid_key:
        return ""

    known = dict(BUILTIN_CONFIRMED_PROFILE_NAME_MAP)
    raw_value = os.getenv("PROFILE_NAME_KNOWN_MAP_JSON", "").strip()
    if raw_value:
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for raw_key, raw_item in parsed.items():
                key = str(raw_key or "").strip()
                name = str(raw_item.get("name") if isinstance(raw_item, dict) else raw_item or "").strip()
                if key and is_valid_profile_name(name):
                    known[key] = name

    return known.get(uid_key, "")


def build_profile_name_urls(resolved: ResolvedInput) -> list[str]:
    candidates: list[str] = []
    username = str(resolved.username or "").strip().strip("/")
    uid = str(resolved.uid or "").strip()

    if username and username.lower() not in {"share", "profile.php"}:
        safe_username = quote(username, safe=".")
        candidates.extend(
            [
                f"https://www.facebook.com/{safe_username}",
                f"https://m.facebook.com/{safe_username}",
                f"https://touch.facebook.com/{safe_username}",
                f"https://mbasic.facebook.com/{safe_username}",
            ]
        )

    if uid:
        candidates.extend(
            [
                f"https://www.facebook.com/profile.php?id={uid}",
                f"https://m.facebook.com/profile.php?id={uid}",
                f"https://touch.facebook.com/profile.php?id={uid}",
                f"https://mbasic.facebook.com/profile.php?id={uid}",
            ]
        )

    canonical = str(resolved.canonical_url or "").strip()
    if canonical:
        candidates.append(canonical)

    return _unique(candidates)


def extract_profile_name(html: str) -> str:
    candidates: list[str] = []
    text = str(html or "")
    if not text:
        return ""

    candidates.extend(_extract_og_title_candidates(text))

    title_match = TITLE_RE.search(text)
    if title_match:
        candidates.append(_text_from_html(title_match.group(1)))

    for match in HEADING_RE.finditer(text):
        candidates.append(_text_from_html(match.group(2)))
        if len(candidates) >= 10:
            break

    for candidate in candidates:
        clean = clean_profile_name_candidate(candidate)
        if is_valid_profile_name(clean):
            return clean
    return ""


def extract_profile_verified_label(html: str, profile_name: str = "") -> str:
    text = str(html or "")
    if not text:
        return ""

    label_from_name = _verified_label_from_name(profile_name)
    if label_from_name:
        return label_from_name

    header = text[:650000]
    lowered = header.lower()
    if "verified account" in lowered and _verified_marker_is_scoped(header, "verified account", profile_name):
        return "Verified account"
    if "t\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh" in lowered and _verified_marker_is_scoped(
        header,
        "t\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh",
        profile_name,
    ):
        return VERIFIED_ACCOUNT_LABEL

    for pattern in VERIFIED_MARKER_PATTERNS:
        match = pattern.search(header)
        if match and _verified_window_is_profile_context(header, match.start(), profile_name):
            return VERIFIED_ACCOUNT_LABEL
    return ""


def is_valid_profile_name(raw_name: str) -> bool:
    name = clean_profile_name_candidate(raw_name)
    if len(name) < 2 or len(name) > 80:
        return False

    low = name.lower()
    if any(str(item).lower() in low for item in PROFILE_NAME_BLOCKLIST):
        return False
    if re.fullmatch(r"[A-Za-zÀ-ỹ\s]+(?:\(\d+\))?", name):
        ui_labels = {
            "tin nhắn",
            "messenger",
            "messages",
            "thông báo",
            "notifications",
            "trang chủ",
            "home",
        }
        if re.sub(r"\s*\(\d+\)\s*$", "", low).strip() in ui_labels:
            return False

    return bool(LETTER_RE.search(name))


def clean_profile_name_candidate(raw_name: str) -> str:
    name = _text_from_html(str(raw_name or ""))
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s+[|·\-]\s+Facebook\s*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"^\(\d+\)\s*", "", name).strip()
    return name


def _display_profile_name_value(name: str) -> str:
    value = clean_profile_name_candidate(name)
    for marker in (
        "Tài khoản đã xác minh",
        "Verified account",
        "TÃ i khoáº£n Ä‘Ã£ xÃ¡c minh",
    ):
        value = value.replace(marker, "").strip()
    return re.sub(r"\s+", " ", value).strip()


def _verified_label_from_name(name: str) -> str:
    lowered = str(name or "").lower()
    if "verified account" in lowered:
        return "Verified account"
    if "tài khoản đã xác minh" in lowered or "tÃ i khoáº£n Ä‘Ã£ xÃ¡c minh" in lowered:
        return VERIFIED_ACCOUNT_LABEL
    return ""


def _verified_marker_is_scoped(header: str, marker: str, profile_name: str = "") -> bool:
    lowered = header.lower()
    marker_lower = marker.lower()
    index = lowered.find(marker_lower)
    while index >= 0:
        if _verified_window_is_profile_context(header, index, profile_name):
            return True
        index = lowered.find(marker_lower, index + len(marker_lower))
    return False


def _verified_window_is_profile_context(header: str, marker_index: int, profile_name: str = "") -> bool:
    window = header[max(0, marker_index - 90000): min(len(header), marker_index + 90000)]
    lowered = window.lower()
    if any(marker in lowered for marker in COMMENT_CONTEXT_MARKERS):
        return False
    if any(marker in lowered for marker in PROFILE_HEADER_CONTEXT_MARKERS):
        return True

    clean_name = _display_profile_name_value(profile_name)
    if clean_name:
        name_index = header.find(clean_name)
        if 0 <= name_index <= 220000 and abs(marker_index - name_index) <= 180000:
            return True

    return marker_index <= 180000


def clear_profile_name_cache() -> None:
    return None


def _extract_og_title_candidates(text: str) -> list[str]:
    out: list[str] = []
    for tag in META_TAG_RE.findall(text):
        attrs = _parse_attrs(tag)
        prop = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        content = attrs.get("content", "")
        if prop == "og:title" and content:
            out.append(content)
    return out


def _parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(tag):
        key = str(match.group(1) or "").lower()
        value = next((group for group in match.groups()[1:] if group is not None), "")
        attrs[key] = html_lib.unescape(value)
    return attrs


def _text_from_html(raw: str) -> str:
    text = html_lib.unescape(str(raw or ""))
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _public_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    }


def _facebook_catalog_headers() -> dict[str, str]:
    headers = _public_headers()
    headers["User-Agent"] = "facebookcatalog/1.0"
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    return headers


def _facebook_externalhit_headers() -> dict[str, str]:
    headers = _public_headers()
    headers["User-Agent"] = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    return headers


def _cookie_mobile_headers(account) -> dict[str, str]:
    headers = _public_headers()
    headers["User-Agent"] = (
        "Mozilla/5.0 (Linux; U; Android 4.0.3; en-us; Galaxy Nexus Build/IML74K) "
        "AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30"
    )
    headers["Cookie"] = cookie_header(account)
    return headers


def _cookie_desktop_headers(account) -> dict[str, str]:
    headers = _public_headers()
    headers["Cookie"] = cookie_header(account)
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    headers["Upgrade-Insecure-Requests"] = "1"
    headers["Sec-Fetch-Site"] = "none"
    headers["Sec-Fetch-Mode"] = "navigate"
    headers["Sec-Fetch-User"] = "?1"
    headers["Sec-Fetch-Dest"] = "document"
    return headers


def _fetch_text(url: str, headers: Mapping[str, str], timeout: float) -> FetchResult:
    try:
        response = requests.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=True,
        )
        return FetchResult(
            http_code=response.status_code,
            text=response.text or "",
            final_url=response.url or url,
            reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
        )
    except requests.RequestException as exc:
        return FetchResult(
            http_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{type(exc).__name__}",
        )


def _fetch_limited_text(url: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> FetchResult:
    try:
        response = requests.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
        text = b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore")
        return FetchResult(
            http_code=response.status_code,
            text=text,
            final_url=response.url or url,
            reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
        )
    except requests.RequestException as exc:
        return FetchResult(
            http_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{type(exc).__name__}",
        )


def _probe_record(
    source: str,
    url: str,
    fetch: FetchResult,
    name: str,
    cookie_account: str = "",
    reason: str | None = None,
    header_label: str = "",
    verified_label: str = "",
) -> dict[str, Any]:
    item = {
        "source": source,
        "url": url,
        "httpCode": fetch.http_code,
        "finalUrl": fetch.final_url,
        "reason": reason or ("name_found" if name else fetch.reason),
        "hasName": bool(name),
    }
    if cookie_account:
        item["cookieAccount"] = cookie_account
    if header_label:
        item["header"] = header_label
    if verified_label:
        item["verifiedLabel"] = verified_label
    return item


def _profile_tick_reason(reason_prefix: str, name: str, verified_label: str, fetch_reason: str) -> str:
    if name and verified_label:
        return f"{reason_prefix}_name_and_verified_found"
    if name:
        return f"{reason_prefix}_name_found"
    if verified_label:
        return f"{reason_prefix}_verified_found"
    return f"{reason_prefix}_{fetch_reason or 'not_found'}"


def _last_probe_http_code(probes: list[dict[str, Any]]) -> int:
    for probe in reversed(probes):
        try:
            return int(probe.get("httpCode") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _canonical_profile_tick_url(normalized: str, uid: str) -> str:
    if uid:
        return f"https://www.facebook.com/profile.php?id={uid}"
    return str(normalized or "").strip()


def _cookie_first_urls(urls: list[str]) -> list[str]:
    return sorted(urls, key=_cookie_url_priority)


def _cookie_probe_candidates(urls: list[str], account) -> list[tuple[str, dict[str, str], str]]:
    ordered_urls = _cookie_first_urls(urls)
    desktop_headers = _cookie_desktop_headers(account)
    mobile_headers = _cookie_mobile_headers(account)
    rounds = [
        ("desktop_logged_in", _www_urls(ordered_urls), desktop_headers),
        ("mobile_logged_in", _mobile_urls(ordered_urls), mobile_headers),
        ("desktop_logged_in", _mobile_urls(ordered_urls), desktop_headers),
        ("mobile_logged_in", _www_urls(ordered_urls), mobile_headers),
    ]

    out: list[tuple[str, dict[str, str], str]] = []
    seen: set[str] = set()
    for header_label, round_urls, headers in rounds:
        for url in round_urls:
            key = f"{header_label}|{url}"
            if key in seen:
                continue
            seen.add(key)
            out.append((url, dict(headers), header_label))
    return out


def _cookie_url_priority(url: str) -> tuple[int, str]:
    value = str(url or "").lower()
    if "www.facebook.com" in value:
        return (0, value)
    if "m.facebook.com" in value:
        return (1, value)
    if "touch.facebook.com" in value:
        return (2, value)
    if "mbasic.facebook.com" in value:
        return (3, value)
    return (4, value)


def _www_urls(urls: list[str]) -> list[str]:
    return [url for url in urls if "www.facebook.com" in str(url or "").lower()]


def _mobile_urls(urls: list[str]) -> list[str]:
    return [url for url in urls if "www.facebook.com" not in str(url or "").lower()]


def _cookie_account_limit() -> int:
    try:
        return max(0, int(os.getenv("PROFILE_NAME_COOKIE_ACCOUNT_LIMIT", "2")))
    except ValueError:
        return 2


def _tick_cookie_account_limit(forced: bool) -> int:
    configured = _cookie_account_limit()
    if configured <= 0:
        return 0
    if forced:
        return configured
    return 1


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
