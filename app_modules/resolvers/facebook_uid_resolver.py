from __future__ import annotations

import html as html_lib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import requests

from app_modules.core.config import get_config


FACEBOOK_HOST_RE = re.compile(r"(^|\.)(facebook\.com|fb\.com)$", re.IGNORECASE)
NUMERIC_UID_RE = re.compile(r"^\d{8,20}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9.]{5,80}$")

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
    r'"userID"\s*:\s*"(\d{8,20})"',
    r'"profile_id"\s*:\s*(\d{8,20})',
    r'"entity_id"\s*:\s*"(\d{8,20})"',
    r'"actorID"\s*:\s*"(\d{8,20})"',
    r'"subject_id"\s*:\s*"(\d{8,20})"',
    r"profile\.php\?id=(\d{8,20})",
    r"fb://profile/(\d{8,20})",
]

FALLBACK_UID_PROBE_USER_AGENTS = [
    "Mozilla/5.0",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
]

DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,vi;q=0.8"


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

    probes: list[dict[str, Any]] = []
    timeout = max(5.0, get_config().request_timeout_seconds)
    for headers in build_uid_probe_header_candidates():
        header_label = _header_label(headers)
        for probe_url in probe_urls:
            fetch_result = _fetch_text(probe_url, headers, timeout)
            probe = {
                "source": "uid_html_probe",
                "url": probe_url,
                "header": header_label,
                "httpCode": fetch_result.http_code,
                "finalUrl": fetch_result.final_url,
                "reason": fetch_result.reason,
            }

            uid_from_html = extract_uid_from_html(fetch_result.text)
            if uid_from_html:
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

            uid_from_final_url = extract_uid_from_url(fetch_result.final_url)
            if uid_from_final_url:
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
    text = str(html_raw or "")
    if not text:
        return ""

    normalized = html_lib.unescape(
        text.replace("\\/", "/").replace("\\u002f", "/").replace("\\u003a", ":")
    )
    for pattern in UID_SCRAPE_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        uid = str(match.group(1) if match.groups() else "").strip()
        if NUMERIC_UID_RE.fullmatch(uid):
            return uid
    return ""


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
        normalized,
        f"https://www.facebook.com{path}{query}",
        f"https://m.facebook.com{path}{query}",
        f"https://mbasic.facebook.com{path}{query}",
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
    candidates = [
        {
            "User-Agent": user_agent,
            "Accept-Language": accept_language or DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        for user_agent in user_agents
        if str(user_agent or "").strip()
    ]

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
    path_value = os.getenv("UID_PROBE_UA_FILE", "").strip()
    if not path_value:
        return []
    try:
        path = Path(path_value)
        if not path.is_file():
            return []
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError:
        return []


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
