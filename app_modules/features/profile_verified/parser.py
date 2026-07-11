from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ProfileState, VerificationState


VERIFIED_LABEL = "Tài khoản đã xác minh"
POSITIVE_PATTERNS = (
    re.compile(r'"show_verified_badge_on_profile"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"is_verified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"isVerified"\s*:\s*true', re.IGNORECASE),
    re.compile(r'show_verified_badge_on_profile\\+"?\s*:\s*true', re.IGNORECASE),
    re.compile(r'is_verified\\+"?\s*:\s*true', re.IGNORECASE),
    re.compile(r'isVerified\\+"?\s*:\s*true', re.IGNORECASE),
)
NEGATIVE_PATTERNS = (
    re.compile(r'"show_verified_badge_on_profile"\s*:\s*false', re.IGNORECASE),
    re.compile(r'"is_verified"\s*:\s*false', re.IGNORECASE),
    re.compile(r'"isVerified"\s*:\s*false', re.IGNORECASE),
)
TEXT_MARKERS = ("verified account", "tài khoản đã xác minh", "tai khoan da xac minh")
PROFILE_CONTEXT = (
    "profile_header_renderer",
    "profilecometheader",
    "xfbprofileentityconvergenceheaderrenderer",
    "profile_header",
    "profile_owner",
    "profile_owner_id",
    "timelineprofile",
    "cometprofileplus",
)
COMMENT_CONTEXT = (
    "cometuficomment",
    "cometcommentnameandbadges",
    "comment_author",
    "comment_id",
    "feedback_comment",
    "profilegeminiweakreferencelink",
    "weak_reference",
)
AUTH_WALL_MARKERS = (
    "/login/?next=",
    "login to facebook",
    "log in to facebook",
    "log in or sign up",
    "đăng nhập",
)
UNAVAILABLE_MARKERS = (
    "this content isn't available",
    "this page isn't available",
    "page not found",
    "content unavailable",
    "the link you followed may be broken",
    "trang này hiện không khả dụng",
)
CHECKPOINT_MARKERS = ("checkpoint", "security check", "identify your account")
STOP_MARKERS = tuple(
    marker.encode("ascii")
    for marker in (
        '"show_verified_badge_on_profile":true',
        '"is_verified":true',
        '"isVerified":true',
    )
)


@dataclass(frozen=True)
class ParsedVerification:
    verification_state: VerificationState
    profile_state: ProfileState
    conclusive: bool
    reason: str


def parse_profile_document(
    text: str,
    final_url: str,
    http_code: int,
    uid: str,
    username: str,
    complete: bool,
    allow_not_verified: bool = True,
) -> ParsedVerification:
    raw = str(text or "")
    lowered = raw.lower()
    combined = f"{str(final_url or '').lower()}\n{lowered[:20000]}"

    if http_code == 404 or any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        return ParsedVerification("UNKNOWN", "UNAVAILABLE", False, "profile_unavailable")
    if any(marker in combined for marker in CHECKPOINT_MARKERS):
        return ParsedVerification("UNKNOWN", "CHECKPOINT", False, "checkpoint_detected")
    if any(marker in combined for marker in AUTH_WALL_MARKERS):
        return ParsedVerification("UNKNOWN", "AUTH_WALL", False, "auth_wall")
    if http_code == 0:
        return ParsedVerification("UNKNOWN", "NETWORK_ERROR", False, "network_error")

    positive = _find_scoped_marker(raw, POSITIVE_PATTERNS, uid)
    if positive or _find_scoped_text_marker(raw):
        return ParsedVerification("VERIFIED", "VISIBLE", True, "verified_marker_found")

    visible = _profile_visible(raw, final_url, uid, username)
    negative = _find_scoped_marker(raw, NEGATIVE_PATTERNS, uid)
    if allow_not_verified and uid and negative:
        return ParsedVerification("NOT_VERIFIED", "VISIBLE" if visible else "UNKNOWN", True, "verified_false_marker_found")
    if allow_not_verified and uid and visible and complete:
        return ParsedVerification("NOT_VERIFIED", "VISIBLE", True, "complete_profile_without_verified_marker")
    if visible:
        return ParsedVerification("UNKNOWN", "VISIBLE", False, "partial_profile_without_verified_marker")
    return ParsedVerification("UNKNOWN", "UNKNOWN", False, "profile_evidence_not_found")


def _find_scoped_marker(text: str, patterns: tuple[re.Pattern[str], ...], uid: str) -> bool:
    header = text[:900_000]
    for pattern in patterns:
        for match in pattern.finditer(header):
            if not _profile_context_at(header, match.start()):
                continue
            if _marker_matches_owner(header, match.start(), uid, pattern):
                return True
    return False


def _find_scoped_text_marker(text: str) -> bool:
    header = text[:900_000]
    lowered = header.lower()
    for marker in TEXT_MARKERS:
        start = lowered.find(marker)
        while start >= 0:
            if _profile_context_at(header, start):
                return True
            start = lowered.find(marker, start + len(marker))
    return False


def _profile_context_at(text: str, index: int) -> bool:
    window = text[max(0, index - 30_000): min(len(text), index + 30_000)].lower()
    if any(marker in window for marker in COMMENT_CONTEXT):
        return False
    return any(marker in window for marker in PROFILE_CONTEXT)


def _marker_matches_owner(text: str, index: int, uid: str, pattern: re.Pattern[str]) -> bool:
    pattern_text = pattern.pattern.lower()
    if "is_verified" not in pattern_text and "isverified" not in pattern_text:
        return True
    clean_uid = str(uid or "").strip()
    if not clean_uid:
        return True
    local = text[max(0, index - 1800): min(len(text), index + 800)]
    id_matches = list(re.finditer(
        r'"(?:id|profile_id|profile_owner_id|profile_owner|actorid)"\s*:\s*"?(\d{1,20})',
        local,
        flags=re.IGNORECASE,
    ))
    if id_matches:
        marker_local_index = min(1800, index)
        nearest = min(id_matches, key=lambda match: abs(match.end() - marker_local_index))
        return nearest.group(1) == clean_uid
    return clean_uid in local


def _profile_visible(text: str, final_url: str, uid: str, username: str) -> bool:
    scope = text[:900_000]
    lowered = scope.lower()
    has_profile_context = any(marker in lowered for marker in PROFILE_CONTEXT)
    if not has_profile_context:
        return False
    clean_uid = str(uid or "").strip()
    clean_username = str(username or "").strip().lower()
    _ = final_url
    if clean_uid and clean_uid in lowered:
        return True
    if clean_username and clean_username in lowered:
        return True
    return False
