from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse

from app_modules.resolvers.facebook_uid_resolver import extract_uid_from_url, normalize_url_input


INVISIBLE_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFE0E\uFE0F\uFEFF]")
RESERVED_PATHS = {"", "profile.php", "login", "share", "people", "photo.php", "permalink.php"}


@dataclass(frozen=True)
class ProfileTarget:
    raw_input: str
    normalized_url: str
    uid: str
    username: str
    canonical_url: str


def normalize_profile_target(raw_input: str) -> ProfileTarget:
    raw = INVISIBLE_RE.sub("", str(raw_input or "").strip()).strip().rstrip("#")
    if raw.isdigit():
        url = f"https://www.facebook.com/profile.php?id={raw}"
        return ProfileTarget(raw, url, raw, "", url)

    if raw and not raw.startswith(("http://", "https://")):
        raw = f"https://www.facebook.com/{quote(raw.strip('/'), safe='.')}"

    normalized = normalize_url_input(raw) or raw
    parsed = urlparse(normalized)
    if parsed.netloc and parsed.netloc.lower() != "www.facebook.com":
        parsed = parsed._replace(netloc="www.facebook.com")
        normalized = urlunparse(parsed)

    login_target = login_next_target(normalized)
    if login_target:
        normalized = login_target

    uid = uid_from_url(normalized)
    username = username_from_url(normalized)
    canonical = f"https://www.facebook.com/profile.php?id={uid}" if uid else normalized.rstrip("#")
    return ProfileTarget(str(raw_input or "").strip(), normalized, uid, username, canonical)


def retarget_profile(current: ProfileTarget, final_url: str) -> ProfileTarget:
    candidate = login_next_target(final_url) or str(final_url or "").strip()
    if not candidate or not _is_facebook_url(candidate):
        return current
    updated = normalize_profile_target(candidate)
    return ProfileTarget(
        raw_input=current.raw_input,
        normalized_url=updated.normalized_url,
        uid=updated.uid or current.uid,
        username=updated.username or current.username,
        canonical_url=(
            f"https://www.facebook.com/profile.php?id={updated.uid or current.uid}"
            if (updated.uid or current.uid)
            else updated.canonical_url or current.canonical_url
        ),
    )


def uid_from_url(url: str) -> str:
    direct = extract_uid_from_url(str(url or ""))
    if direct:
        return direct
    parsed = urlparse(str(url or ""))
    query_uid = (parse_qs(parsed.query or "").get("id") or [""])[0].strip()
    if query_uid.isdigit():
        return query_uid
    first = (parsed.path or "").strip("/").split("/")[0].strip()
    return first if first.isdigit() else ""


def username_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    first = (parsed.path or "").strip("/").split("/")[0].strip()
    if not first or first.lower() in RESERVED_PATHS or first.isdigit():
        return ""
    return first


def login_next_target(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.path.rstrip("/").lower() != "/login":
        return ""
    raw_next = (parse_qs(parsed.query or "").get("next") or [""])[0]
    target = unquote(raw_next).strip()
    return target if _is_facebook_url(target) else ""


def public_candidate_urls(target: ProfileTarget) -> list[str]:
    if target.uid:
        return _unique([
            f"https://www.facebook.com/profile.php?id={target.uid}&sk=about",
            f"https://www.facebook.com/profile.php?id={target.uid}",
        ])
    if target.username:
        safe = quote(target.username, safe=".")
        return _unique([
            f"https://www.facebook.com/{safe}/about",
            f"https://www.facebook.com/{safe}",
        ])
    return _unique([target.normalized_url])


def cookie_candidate_urls(target: ProfileTarget) -> list[str]:
    candidates: list[str] = []
    # A profile.php request often redirects to the canonical username but only
    # returns Facebook's generic shell. Once the username is known, request it
    # directly so the profile header (and verification field) is hydrated.
    if target.username:
        safe = quote(target.username, safe=".")
        candidates.extend([
            f"https://www.facebook.com/{safe}",
            f"https://www.facebook.com/{safe}/about",
        ])
    if target.uid:
        candidates.extend([
            f"https://www.facebook.com/profile.php?id={target.uid}",
            f"https://www.facebook.com/profile.php?id={target.uid}&sk=about",
        ])
    candidates.append(target.normalized_url)
    return _unique(candidates)


def _is_facebook_url(value: str) -> bool:
    try:
        host = urlparse(str(value or "")).netloc.lower().split(":", 1)[0]
    except ValueError:
        return False
    return host == "facebook.com" or host.endswith(".facebook.com")


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out
