from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse


VERIFIED_ACCOUNT_LABEL = "T\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh"

VERIFIED_MARKERS = (
    "verified account",
    "t\u00e0i kho\u1ea3n \u0111\u00e3 x\u00e1c minh",
    "tai khoan da xac minh",
    '"show_verified_badge_on_profile":true',
    '"is_verified":true',
    '"isVerified":true',
    "show_verified_badge_on_profile\\\":true",
    "is_verified\\\":true",
    "isVerified\\\":true",
)

VERIFIED_MARKER_PATTERNS = (
    re.compile(r'"show_verified_badge_on_profile"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"is_verified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"isVerified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'show_verified_badge_on_profile\\+"\s*:\s*true', re.IGNORECASE),
    re.compile(r'is_verified\\+"\s*:\s*true', re.IGNORECASE),
    re.compile(r'isVerified\\+"\s*:\s*true', re.IGNORECASE),
)

VERIFIED_MARKER_BYTES = tuple(
    marker.lower().encode("utf-8", errors="ignore")
    for marker in VERIFIED_MARKERS
    if marker.isascii()
)

AUTH_WALL_MARKERS = (
    "/login/?next=",
    "login to facebook",
    "log in to facebook",
    "log in or sign up",
    "dang nhap",
    "\u0111\u0103ng nh\u1eadp",
)

UNAVAILABLE_MARKERS = (
    "this content isn't available",
    "this page isn't available",
    "page not found",
    "content unavailable",
    "the link you followed may be broken",
    "trang n\u00e0y kh\u00f4ng hi\u1ec3n th\u1ecb",
)


def verified_label(text: str) -> str:
    raw = str(text or "")
    lowered = raw.lower()
    if not lowered:
        return ""
    if any(marker.lower() in lowered for marker in VERIFIED_MARKERS):
        return VERIFIED_ACCOUNT_LABEL
    if any(pattern.search(raw) for pattern in VERIFIED_MARKER_PATTERNS):
        return VERIFIED_ACCOUNT_LABEL
    legacy = legacy_verified_label(raw)
    if legacy:
        return VERIFIED_ACCOUNT_LABEL
    return ""


def legacy_verified_label(text: str) -> str:
    """Reuse the older scoped parser on already-fetched HTML.

    This keeps the fast network path while preserving accuracy for Facebook
    pages whose verified badge is not exposed as a compact JSON marker.
    """

    try:
        from app_modules.features import profile_tick as legacy_tick

        label = legacy_tick.extract_profile_verified_label(text, "", "")
        if label:
            return label
        scope = text[: min(len(text), 900_000)]
        name = legacy_tick.extract_profile_name(scope)
        if name:
            return legacy_tick.extract_profile_verified_label(text, name, "")
    except Exception:
        return ""
    return ""


def has_verified_hint(text: str) -> bool:
    return bool(verified_label(text))


def auth_wall_reason(text: str, final_url: str = "") -> str:
    value = f"{final_url}\n{text[:12000]}".lower()
    if any(marker in value for marker in AUTH_WALL_MARKERS):
        return "auth_wall"
    return ""


def unavailable_reason(text: str, http_code: int = 0) -> str:
    if int(http_code or 0) == 404:
        return "http_404"
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        return "profile_unavailable"
    return ""


def profile_seen(text: str, final_url: str, uid: str, username: str) -> bool:
    lowered = str(text or "").lower()
    final = str(final_url or "").lower()
    if uid and (uid in lowered or uid in final):
        return True
    if username and (username.lower() in lowered or username.lower() in final):
        return True
    return any(marker in lowered for marker in ("profile_owner", "profilecomet", "timelineprofile"))


def login_next_target(final_url: str) -> str:
    parsed = urlparse(str(final_url or ""))
    query = parse_qs(parsed.query or "")
    raw = (query.get("next") or [""])[0]
    if not raw:
        return ""
    target = unquote(raw)
    if not target.startswith("http"):
        return ""
    host = urlparse(target).netloc.lower()
    if not host.endswith("facebook.com"):
        return ""
    return target


def strip_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    return re.sub(r"\s+", " ", text).strip()
