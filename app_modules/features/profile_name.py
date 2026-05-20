from __future__ import annotations

import html as html_lib
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote

import requests

from app_modules.checkers.live_die import LiveDieResult
from app_modules.core.config import get_config
from app_modules.resolvers.facebook_cookies import cookie_header, load_cookie_accounts
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

MAX_NAME_CACHE_ITEMS = 1000
VERIFIED_ACCOUNT_LABEL = "T\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh"
VERIFIED_MARKER_PATTERNS = [
    re.compile(r'"show_verified_badge_on_profile"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"is_verified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"isVerified"\s*:\s*true', re.IGNORECASE),
]
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
_NAME_CACHE: OrderedDict[str, CachedProfileName] = OrderedDict()


@dataclass(frozen=True)
class ProfileNameResult:
    name: str
    source: str
    reason: str
    probes: list[dict[str, Any]] = field(default_factory=list)
    verified_label: str = ""


@dataclass(frozen=True)
class CachedProfileName:
    name: str
    verified_label: str = ""
    verified_checked: bool = False


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
            _cache_put(uid, known_name, verified_checked=False)
            return ProfileNameResult(known_name, "profile_name_known_map", "name_found_known_map")

        cached = _cache_get_result(uid, include_verified=include_verified)
        if cached:
            return cached

    urls = build_profile_name_urls(resolved)
    if not urls:
        resolver_name = str(getattr(resolved, "resolver_name", "") or "").strip()
        if resolver_name and is_valid_profile_name(resolver_name):
            _cache_put(uid, resolver_name)
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
            verified_label = extract_profile_verified_label(fetch.text)
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
                _cache_put(uid, name, verified_label=verified_label, verified_checked=True)
                return ProfileNameResult(
                    name,
                    "profile_name_cookie",
                    "name_found_cookie",
                    probes,
                    verified_label=verified_label,
                )

    if known_name:
        _cache_put(uid, known_name, verified_checked=False)
        return ProfileNameResult(known_name, "profile_name_known_map", "name_found_known_map", probes)

    resolver_name = str(getattr(resolved, "resolver_name", "") or "").strip()
    if resolver_name and is_valid_profile_name(resolver_name):
        _cache_put(uid, resolver_name, verified_checked=False)
        return ProfileNameResult(resolver_name, "resolver_name", "name_found_resolver", probes)

    return ProfileNameResult("", "profile_name", "name_not_found", probes)


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


def extract_profile_verified_label(html: str) -> str:
    text = str(html or "")
    if not text:
        return ""

    lowered = text.lower()
    if "verified account" in lowered:
        return "Verified account"
    if "t\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh" in lowered:
        return VERIFIED_ACCOUNT_LABEL

    for pattern in VERIFIED_MARKER_PATTERNS:
        if pattern.search(text):
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


def clear_profile_name_cache() -> None:
    _NAME_CACHE.clear()


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


def _cache_get_result(uid: str, include_verified: bool = False) -> ProfileNameResult | None:
    if not uid:
        return None
    cached = _NAME_CACHE.get(uid)
    if cached:
        if not is_valid_profile_name(cached.name):
            _NAME_CACHE.pop(uid, None)
            return None
        if include_verified and not cached.verified_checked:
            return None
        _NAME_CACHE.move_to_end(uid)
        return ProfileNameResult(
            cached.name,
            "uid_name_cache",
            "cache_hit",
            verified_label=cached.verified_label,
        )
    return None


def _cache_put(
    uid: str,
    name: str,
    verified_label: str = "",
    verified_checked: bool = False,
) -> None:
    if not uid or not name:
        return
    _NAME_CACHE[uid] = CachedProfileName(
        name=name,
        verified_label=verified_label,
        verified_checked=verified_checked,
    )
    _NAME_CACHE.move_to_end(uid)
    while len(_NAME_CACHE) > MAX_NAME_CACHE_ITEMS:
        _NAME_CACHE.popitem(last=False)


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
