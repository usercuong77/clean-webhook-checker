from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

import requests

from app_modules.core.config import get_config
from app_modules.resolvers.tds_uid_resolver import resolve_uid_with_tds_api


FACEBOOK_HOST_RE = re.compile(r"(^|\.)(facebook\.com|fb\.com)$", re.IGNORECASE)
NUMERIC_UID_RE = re.compile(r"^\d{1,20}$")
GENERIC_NUMERIC_UID_RE = re.compile(r"^\d{5,20}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9.]{3,80}$")
DEFAULT_UID_PROBE_UA_FILE = Path(__file__).resolve().parents[2] / "config" / "uid_probe_user_agents.txt"

RESERVED_FIRST_PATHS = {
    "",
    "profile.php",
    "pages",
    "pg",
    "groups",
    "watch",
    "gaming",
    "marketplace",
    "messages",
    "notifications",
    "friends",
    "reel",
    "reels",
    "stories",
    "story.php",
    "share",
    "permalink.php",
    "photo.php",
    "photos",
    "login",
    "help",
    "privacy",
}

UID_SCRAPE_PATTERNS = [
    r'<meta[^>]+property=["\']al:ios:url["\'][^>]+content=["\']fb://profile/(\d{5,20})',
    r'<meta[^>]+property=["\']al:android:url["\'][^>]+content=["\']fb://profile/(\d{5,20})',
    r'<meta[^>]+property=["\']al:web:url["\'][^>]+content=["\']fb://profile/(\d{5,20})',
    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']https?://(?:www\.)?facebook\.com/profile\.php\?id=(\d{5,20})',
    r'"profile_owner"\s*:\s*"(\d{5,20})"',
    r'"profile_owner_id"\s*:\s*"(\d{5,20})"',
    r'"owner"\s*:\s*\{\s*"id"\s*:\s*"(\d{5,20})"',
    r'"ownerID"\s*:\s*"(\d{5,20})"',
    r'"profileID"\s*:\s*"(\d{5,20})"',
    r'"user_id"\s*:\s*"(\d{5,20})"',
    r'"userID"\s*:\s*"(\d{5,20})"',
    r'"profile_id"\s*:\s*(\d{5,20})',
    r'"profile_id"\s*:\s*"(\d{5,20})"',
    r'"entity_id"\s*:\s*"(\d{5,20})"',
    r'"actorID"\s*:\s*"(\d{5,20})"',
    r'"subject_id"\s*:\s*"(\d{5,20})"',
    r"profile\.php\?id=(\d{5,20})",
    r"fb://profile/(\d{5,20})",
]

UID_META_SCRAPE_PATTERNS = [
    r'<meta[^>]+property=["\']al:ios:url["\'][^>]+content=["\']fb://profile/(\d{1,20})',
    r'<meta[^>]+property=["\']al:android:url["\'][^>]+content=["\']fb://profile/(\d{1,20})',
    r'<meta[^>]+property=["\']al:web:url["\'][^>]+content=["\']fb://profile/(\d{1,20})',
    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']https?://(?:www\.)?facebook\.com/profile\.php\?id=(\d{1,20})',
    r"fb://profile/(\d{1,20})",
]

FALLBACK_UID_PROBE_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Mozilla/5.0",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
]

DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,vi;q=0.8"
DEFAULT_UID_PUBLIC_PROBE_TIMEOUT_SEC = 4.0
DEFAULT_UID_PUBLIC_PROBE_DEADLINE_SEC = 14.0
DEFAULT_UID_PUBLIC_PROBE_MAX_REQUESTS = 12
KNOWN_UID_MAP_ENV_KEYS = (
    "UID_RESOLVER_KNOWN_MAP_JSON",
    "UID_RESOLVER_KNOWN_UID_MAP_JSON",
)

BUILTIN_CONFIRMED_UID_MAP = {
    "hong.duyen.tran.594446": "100004192098772",
    "ng.trinh.498077": "100080441816993",
    "vo.duy.0910": "100010211341364",
    "caubeoooo": "100041007767995",
    "tankiet.pham.1276": "100042281496124",
    "love.over.219161": "61560438496711",
    "bien.trang.750": "100004507923562",
    "thanhcuongmedia": "100002614628083",
    "zminhhuydev": "9209278",
}


@dataclass(frozen=True)
class DirectUid:
    uid: str
    source: str
    reason: str


@dataclass(frozen=True)
class FetchResult:
    http_code: int
    text: str
    final_url: str
    reason: str


@dataclass(frozen=True)
class UidResolution:
    input: str
    uid: str
    username: str
    canonical_url: str
    source: str
    reason: str
    probes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.uid)


def resolve_uid_from_any_input(raw: Any) -> UidResolution:
    value = str(raw or "").strip()
    if not value:
        return UidResolution("", "", "", "", "uid_resolver", "empty_input")

    direct_uid = normalize_uid(value)
    if direct_uid:
        return _uid_result(value, direct_uid, "", "direct_uid", "numeric_uid", [])

    normalized = normalize_url_input(value)
    direct_from_url = _extract_uid_from_url_detail(normalized)
    if direct_from_url:
        return _uid_result(
            value,
            direct_from_url.uid,
            "",
            direct_from_url.source,
            direct_from_url.reason,
            [],
        )

    probe_urls = build_facebook_probe_urls(normalized)
    username = extract_username_from_url(normalized)
    if not probe_urls:
        return UidResolution(value, "", username, "", "uid_resolver", "not_facebook_url")

    known_uid = _resolve_uid_from_known_map(value, normalized, username)
    if known_uid:
        return _uid_result(
            value,
            known_uid,
            username,
            "uid_known_map",
            "uid_found_in_known_map",
            [],
        )

    tds_result = resolve_uid_with_tds_api(normalized)
    tds_probe = {
        "source": tds_result.source,
        "httpCode": tds_result.http_code,
        "reason": tds_result.reason,
    }
    if tds_result.name:
        tds_probe["name"] = tds_result.name
    if tds_result.uid:
        tds_probe["foundUid"] = tds_result.uid
        return _uid_result(
            value,
            tds_result.uid,
            username,
            tds_result.source,
            tds_result.reason,
            [tds_probe],
        )

    probes: list[dict[str, Any]] = []
    if tds_result.reason not in {"empty_input"}:
        probes.append(tds_probe)
    timeout = _uid_public_probe_timeout()
    deadline_at = time.monotonic() + _uid_public_probe_deadline()
    max_requests = _uid_public_probe_max_requests()
    request_count = 0
    for headers in build_uid_probe_header_candidates():
        if request_count >= max_requests or _remaining_timeout(deadline_at, timeout) <= 0:
            break
        header_label = _header_label(headers)
        for probe_url in probe_urls:
            if request_count >= max_requests:
                break
            request_timeout = _remaining_timeout(deadline_at, timeout)
            if request_timeout <= 0:
                break
            request_count += 1
            fetch_result = _fetch_text(probe_url, headers, request_timeout)
            probe = {
                "source": "uid_html_probe",
                "url": probe_url,
                "header": header_label,
                "httpCode": fetch_result.http_code,
                "finalUrl": fetch_result.final_url,
                "reason": fetch_result.reason,
            }

            uid_from_final_url = extract_uid_from_url(fetch_result.final_url)
            if uid_from_final_url:
                if username and not _verify_uid_matches_requested_slug(
                    uid_from_final_url,
                    normalized,
                    headers,
                    _remaining_timeout(deadline_at, timeout),
                ):
                    probe["candidateUid"] = uid_from_final_url
                    probe["reason"] = "uid_final_url_rejected_by_slug_verification"
                    probes.append(probe)
                    continue
                probe["foundUid"] = uid_from_final_url
                probe["reason"] = "uid_found_in_final_url"
                probes.append(probe)
                return _uid_result(
                    value,
                    uid_from_final_url,
                    username,
                    "uid_final_url",
                    "uid_found_in_final_url",
                    probes,
                )

            uid_for_username = extract_uid_for_username_from_html(fetch_result.text, username)
            if uid_for_username:
                probe["foundUid"] = uid_for_username
                probe["reason"] = "uid_found_for_username_in_html"
                probes.append(probe)
                return _uid_result(
                    value,
                    uid_for_username,
                    username,
                    "uid_html_probe",
                    "uid_found_for_username_in_html",
                    probes,
                )

            uid_from_meta = extract_uid_from_meta_html(fetch_result.text)
            if uid_from_meta:
                if username and not _verify_uid_matches_requested_slug(
                    uid_from_meta,
                    normalized,
                    headers,
                    _remaining_timeout(deadline_at, timeout),
                ):
                    probe["candidateUid"] = uid_from_meta
                    probe["reason"] = "uid_meta_rejected_by_slug_verification"
                    probes.append(probe)
                    continue
                probe["foundUid"] = uid_from_meta
                probe["reason"] = "uid_found_in_meta_html"
                probes.append(probe)
                return _uid_result(
                    value,
                    uid_from_meta,
                    username,
                    "uid_html_probe",
                    "uid_found_in_meta_html",
                    probes,
                )

            uid_from_html = extract_uid_from_html(fetch_result.text)
            if uid_from_html:
                if username and not _verify_uid_matches_requested_slug(
                    uid_from_html,
                    normalized,
                    headers,
                    _remaining_timeout(deadline_at, timeout),
                ):
                    probe["candidateUid"] = uid_from_html
                    probe["reason"] = "uid_candidate_rejected_by_slug_verification"
                    probes.append(probe)
                    continue
                probe["foundUid"] = uid_from_html
                probe["reason"] = "uid_found_in_html"
                probes.append(probe)
                return _uid_result(
                    value,
                    uid_from_html,
                    username,
                    "uid_html_probe",
                    "uid_found_in_html",
                    probes,
                )

            probes.append(probe)

    cookie_result = _resolve_uid_with_cookie_fallback(normalized)
    if cookie_result.uid:
        return _uid_result(
            value,
            cookie_result.uid,
            username,
            cookie_result.source,
            cookie_result.reason,
            probes + cookie_result.probes,
        )

    return UidResolution(
        input=value,
        uid="",
        username=username,
        canonical_url=_canonical_from_normalized(normalized),
        source="uid_resolver",
        reason=_final_uid_not_found_reason(cookie_result.reason),
        probes=probes + cookie_result.probes,
    )


def normalize_uid(uid_raw: Any) -> str:
    uid = str(uid_raw or "").strip()
    return uid if NUMERIC_UID_RE.fullmatch(uid) else ""


def normalize_url_input(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if normalize_uid(value):
        return value
    if _looks_like_bare_username(value):
        return f"https://www.facebook.com/{value}"
    if re.match(r"^https?://", value, re.IGNORECASE):
        return value
    return f"https://{value}"


def extract_uid_from_url(url_raw: Any) -> str:
    direct = _extract_uid_from_url_detail(url_raw)
    return direct.uid if direct else ""


def extract_username_from_url(url_raw: Any) -> str:
    normalized = normalize_url_input(url_raw)
    parsed = _parse_facebook_url(normalized)
    if not parsed:
        return ""

    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return ""

    first = parts[0].strip()
    first_lower = first.lower()
    if first_lower in RESERVED_FIRST_PATHS:
        return ""
    if NUMERIC_UID_RE.fullmatch(first):
        return ""
    return first if USERNAME_RE.fullmatch(first) else ""


def extract_uid_from_html(html_raw: Any) -> str:
    candidates = extract_uid_candidates_from_html(html_raw)
    return candidates[0] if candidates else ""


def extract_uid_candidates_from_html(html_raw: Any) -> list[str]:
    normalized = normalize_facebook_payload_text(html_raw)
    if not normalized:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in UID_SCRAPE_PATTERNS:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            uid = str(match.group(1) if match.groups() else "").strip()
            if not GENERIC_NUMERIC_UID_RE.fullmatch(uid) or uid in seen:
                continue
            seen.add(uid)
            candidates.append(uid)
    return candidates


def extract_uid_from_meta_html(html_raw: Any) -> str:
    normalized = normalize_facebook_payload_text(html_raw)
    if not normalized:
        return ""

    scan_text = normalized[:120000]
    if not any(marker in scan_text for marker in ("fb://profile/", "og:url", "al:ios:url", "al:android:url", "al:web:url")):
        return ""

    for pattern in UID_META_SCRAPE_PATTERNS:
        match = re.search(pattern, scan_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        uid = str(match.group(1) or "").strip()
        if NUMERIC_UID_RE.fullmatch(uid):
            return uid
    return ""


def extract_uid_for_username_from_html(html_raw: Any, username: Any) -> str:
    normalized = normalize_facebook_payload_text(html_raw)
    slug = str(username or "").strip().lower().strip("/")
    if not normalized or not USERNAME_RE.fullmatch(slug):
        return ""

    escaped_slug = re.escape(slug)
    direct_patterns = (
        rf'"userVanity"\s*:\s*"{escaped_slug}".{{0,1600}}?"userID"\s*:\s*"(\d{{1,20}})"',
        rf'"userID"\s*:\s*"(\d{{1,20}})".{{0,1600}}?"userVanity"\s*:\s*"{escaped_slug}"',
        rf'"vanity"\s*:\s*"{escaped_slug}".{{0,800}}?"id"\s*:\s*"(\d{{1,20}})"',
        rf'"id"\s*:\s*"(\d{{1,20}})".{{0,800}}?"vanity"\s*:\s*"{escaped_slug}"',
    )
    for pattern in direct_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            uid = str(match.group(1) or "").strip()
            if NUMERIC_UID_RE.fullmatch(uid):
                return uid

    for slug_match in re.finditer(escaped_slug, normalized, flags=re.IGNORECASE):
        start = max(0, slug_match.start() - 1200)
        end = min(len(normalized), slug_match.end() + 2200)
        window = normalized[start:end]
        for pattern in (
            r'"profile_owner"\s*:\s*\{\s*"id"\s*:\s*"(\d{1,20})"',
            r'"profile_owner"\s*:\s*"(\d{1,20})"',
            r'"userID"\s*:\s*"(\d{1,20})"',
        ):
            match = re.search(pattern, window, flags=re.IGNORECASE | re.DOTALL)
            if match:
                uid = str(match.group(1) or "").strip()
                if NUMERIC_UID_RE.fullmatch(uid):
                    return uid
    return ""


def _verify_uid_matches_requested_slug(
    uid: str,
    raw: Any,
    headers: Mapping[str, str],
    timeout: float,
) -> bool:
    slug = extract_username_from_url(raw).strip().lower()
    if not slug:
        return True
    if timeout <= 0:
        return False

    fetch_result = _fetch_text(
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


def _uid_public_probe_timeout() -> float:
    return _env_float("UID_PUBLIC_PROBE_TIMEOUT_SEC", DEFAULT_UID_PUBLIC_PROBE_TIMEOUT_SEC)


def _uid_public_probe_deadline() -> float:
    return _env_float("UID_PUBLIC_PROBE_DEADLINE_SEC", DEFAULT_UID_PUBLIC_PROBE_DEADLINE_SEC)


def _uid_public_probe_max_requests() -> int:
    return max(1, int(_env_float("UID_PUBLIC_PROBE_MAX_REQUESTS", DEFAULT_UID_PUBLIC_PROBE_MAX_REQUESTS)))


def _remaining_timeout(deadline_at: float, preferred_timeout: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        return 0.0
    return max(0.5, min(preferred_timeout, remaining))


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def normalize_facebook_payload_text(raw: Any) -> str:
    text = str(raw or "")
    if not text:
        return ""
    normalized = html_lib.unescape(text)
    replacements = {
        "\\/": "/",
        "\\u002f": "/",
        "\\u002F": "/",
        "\\u003a": ":",
        "\\u003A": ":",
        "\\u003d": "=",
        "\\u003D": "=",
        "\\u0026": "&",
        "\\u003f": "?",
        "\\u003F": "?",
        "\\x2f": "/",
        "\\x2F": "/",
        "\\x3a": ":",
        "\\x3A": ":",
        "\\x3d": "=",
        "\\x3D": "=",
        "\\x26": "&",
        "\\x3f": "?",
        "\\x3F": "?",
        "&#47;": "/",
        "&#58;": ":",
        "&#61;": "=",
        "&#38;": "&",
        "&#63;": "?",
        "%253d": "%3d",
        "%253D": "%3D",
        "%2526": "%26",
        "%253f": "%3f",
        "%253F": "%3F",
        "%3d": "=",
        "%3D": "=",
        "%26": "&",
        "%3f": "?",
        "%3F": "?",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    for _ in range(2):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    return normalized


def build_facebook_probe_urls(url_raw: Any) -> list[str]:
    normalized = normalize_url_input(url_raw)
    if normalize_uid(normalized):
        return [f"https://www.facebook.com/profile.php?id={normalized}"]

    parsed = _parse_facebook_url(normalized)
    if not parsed:
        return []

    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    candidates = [
        f"https://mbasic.facebook.com{path}{query}",
        f"https://m.facebook.com{path}{query}",
        f"https://www.facebook.com{path}{query}",
        normalized,
    ]

    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def build_uid_probe_header_candidates() -> list[dict[str, str]]:
    accept_language = os.getenv("UID_PROBE_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE).strip()
    user_agents = _load_user_agents_from_file() + FALLBACK_UID_PROBE_USER_AGENTS
    candidates = []
    for user_agent in user_agents:
        user_agent = str(user_agent or "").strip()
        if not user_agent:
            continue
        headers = {
            "User-Agent": user_agent,
            "Accept-Language": accept_language or DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        headers.update(build_facebook_navigation_hint_headers(user_agent))
        candidates.append(headers)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        key = (
            f"{item.get('User-Agent', '').strip().lower()}|"
            f"{item.get('Accept-Language', '').strip().lower()}"
        )
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_facebook_navigation_hint_headers(user_agent: str) -> dict[str, str]:
    value = str(user_agent or "").lower()
    platform = '"Windows"'
    mobile = "?0"
    if "android" in value:
        platform = '"Android"'
        mobile = "?1"
    elif "iphone" in value or "ipad" in value or "ios" in value:
        platform = '"iOS"'
        mobile = "?1"
    return {
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="140", "Not.A/Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": mobile,
        "sec-ch-ua-platform": platform,
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.facebook.com/",
    }


def _extract_uid_from_url_detail(url_raw: Any) -> DirectUid | None:
    value = str(url_raw or "").strip()
    direct_uid = normalize_uid(value)
    if direct_uid:
        return DirectUid(direct_uid, "direct_uid", "numeric_uid")

    normalized = normalize_url_input(value)
    parsed = _parse_facebook_url(normalized)
    if not parsed:
        return None

    query = parse_qs(parsed.query or "")
    profile_id = _first_numeric(query.get("id")) or _first_numeric(query.get("profile_id"))
    if profile_id:
        return DirectUid(profile_id, "profile_php", "query_id")

    parts = [part for part in (parsed.path or "").strip("/").split("/") if part]
    if not parts:
        return None

    first = parts[0].lower()
    if first == "people" and len(parts) >= 3:
        people_uid = normalize_uid(parts[2])
        if people_uid:
            return DirectUid(people_uid, "people_url", "people_path_uid")

    path_uid = normalize_uid(parts[0])
    if path_uid:
        return DirectUid(path_uid, "numeric_path", "path_uid")

    return None


def _parse_facebook_url(url_raw: Any):
    value = str(url_raw or "").strip()
    if not value or normalize_uid(value):
        return None
    try:
        parsed = urlparse(value if re.match(r"^https?://", value, re.IGNORECASE) else f"https://{value}")
    except Exception:
        return None
    host = _canonical_host(parsed.netloc)
    if not FACEBOOK_HOST_RE.search(host):
        return None
    return parsed


def _first_numeric(values: list[str] | None) -> str:
    for item in values or []:
        uid = normalize_uid(item)
        if uid:
            return uid
    return ""


def _looks_like_bare_username(value: str) -> bool:
    if "/" in value or "?" in value or ":" in value:
        return False
    if "." in value and not USERNAME_RE.fullmatch(value):
        return False
    return bool(USERNAME_RE.fullmatch(value))


def _canonical_host(netloc: str) -> str:
    host = (netloc or "").lower().split("@")[-1].split(":", 1)[0]
    for prefix in ("www.", "m.", "mbasic.", "touch."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def _canonical_from_normalized(normalized: str) -> str:
    parsed = _parse_facebook_url(normalized)
    if not parsed:
        return ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://www.facebook.com{path}{query}"


def _resolve_uid_from_known_map(raw_input: str, normalized: str, username: str) -> str:
    known_map = _load_known_uid_map()
    if not known_map:
        return ""

    for key in _known_uid_lookup_keys(raw_input, normalized, username):
        uid = known_map.get(key)
        if uid:
            return uid
    return ""


def _load_known_uid_map() -> dict[str, str]:
    out: dict[str, str] = dict(BUILTIN_CONFIRMED_UID_MAP)
    raw_value = ""
    for key in KNOWN_UID_MAP_ENV_KEYS:
        raw_value = os.getenv(key, "").strip()
        if raw_value:
            break
    if not raw_value:
        return out

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return out
    if not isinstance(parsed, dict):
        return out

    for raw_key, raw_item in parsed.items():
        key = str(raw_key or "").strip().lower().rstrip("/")
        uid_value = raw_item.get("uid") if isinstance(raw_item, dict) else raw_item
        uid = normalize_uid(uid_value)
        if key and uid:
            out[key] = uid
    return out


def _known_uid_lookup_keys(raw_input: str, normalized: str, username: str) -> list[str]:
    candidates = [
        raw_input,
        normalized,
        _canonical_from_normalized(normalized),
        username,
    ]

    parsed = _parse_facebook_url(normalized)
    if parsed:
        path = (parsed.path or "").strip("/")
        if path:
            candidates.extend([path, unquote(path)])

    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item or "").strip().lower().rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _uid_result(
    raw_input: str,
    uid: str,
    username: str,
    source: str,
    reason: str,
    probes: list[dict[str, Any]],
) -> UidResolution:
    return UidResolution(
        input=raw_input,
        uid=uid,
        username=username,
        canonical_url=f"https://www.facebook.com/profile.php?id={uid}",
        source=source,
        reason=reason,
        probes=probes,
    )


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


def _load_user_agents_from_file() -> list[str]:
    paths: list[Path] = []
    path_value = os.getenv("UID_PROBE_UA_FILE", "").strip()
    if path_value:
        paths.append(Path(path_value))
    paths.append(DEFAULT_UID_PROBE_UA_FILE)

    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                item = line.strip()
                if not item or item.startswith("#") or item in seen:
                    continue
                seen.add(item)
                out.append(item)
        except OSError:
            continue
    return out


def _header_label(headers: Mapping[str, str]) -> str:
    user_agent = str(headers.get("User-Agent", "")).strip()
    if not user_agent:
        return "no_user_agent"
    return user_agent[:80]


def _resolve_uid_with_cookie_fallback(normalized: str):
    from app_modules.resolvers.facebook_uid_cookie_resolver import resolve_uid_with_cookies

    return resolve_uid_with_cookies(normalized)


def _final_uid_not_found_reason(cookie_reason: str) -> str:
    if cookie_reason == "no_usable_cookie_accounts":
        return "uid_not_found_after_public_probe_no_cookie_accounts"
    if cookie_reason:
        return "uid_not_found_after_public_and_cookie_probe"
    return "uid_not_found_after_probe"
