from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

import requests

from app_modules.core.config import get_config
from app_modules.resolvers.facebook_cookies import CookieAccount, load_cookie_accounts
from app_modules.resolvers.facebook_uid_resolver import normalize_uid
from app_modules.resolvers.uid_resolver import ResolvedInput


LATEST_POST_PAIR_PATTERNS = [
    r'"post_id"\s*:\s*"([A-Za-z0-9_]{8,})"[\s\S]{0,2000}?"publish_time"\s*:\s*(\d{9,13})',
    r'"top_level_post_id"\s*:\s*"([A-Za-z0-9_]{8,})"[\s\S]{0,2000}?"publish_time"\s*:\s*(\d{9,13})',
    r'"story_fbid"\s*:\s*"([A-Za-z0-9_]{8,})"[\s\S]{0,2000}?"publish_time"\s*:\s*(\d{9,13})',
    r'"legacy_fbid"\s*:\s*"([A-Za-z0-9_]{8,})"[\s\S]{0,2000}?"publish_time"\s*:\s*(\d{9,13})',
]

LATEST_POST_ID_PATTERNS = [
    r'"post_id"\s*:\s*"([A-Za-z0-9_]{8,})"',
    r'"post_id"\s*:\s*(\d{8,})',
    r'"top_level_post_id"\s*:\s*"([A-Za-z0-9_]{8,})"',
    r'"top_level_post_id"\s*:\s*(\d{8,})',
    r'"story_fbid"\s*:\s*"([A-Za-z0-9_]{8,})"',
    r'"story_fbid"\s*:\s*(\d{8,})',
    r'"legacy_fbid"\s*:\s*"([A-Za-z0-9_]{8,})"',
    r'"legacy_fbid"\s*:\s*(\d{8,})',
    r'(?:^|[?&]|%3f|%26)story_fbid(?:=|%3d)([A-Za-z0-9_]{8,})',
    r'permalink\.php(?:\?|%3f)[^"\'\s<>]*?(?:[?&]|%26)story_fbid(?:=|%3d)([A-Za-z0-9_]{8,})',
    r"/posts/([A-Za-z0-9_]{8,})",
    r'(?:^|[?&]|%3f|%26)fbid(?:=|%3d)(\d{8,})',
]

LATEST_POST_TIME_PATTERNS = [
    r'"publish_time"\s*:\s*(\d{9,13})',
    r'"creation_time"\s*:\s*(\d{9,13})',
    r'"created_time"\s*:\s*(\d{9,13})',
    r"\bdata-utime\s*=\s*\"(\d{9,13})\"",
]

POST_CONTENT_PATTERNS = [
    r'"message"\s*:\s*\{[^{}]{0,4000}?"text"\s*:\s*"((?:\\.|[^"\\]){1,5000})"',
    r'"post_message"\s*:\s*\{[^{}]{0,4000}?"text"\s*:\s*"((?:\\.|[^"\\]){1,5000})"',
    r'"creation_story"\s*:\s*\{[^{}]{0,5000}?"text"\s*:\s*"((?:\\.|[^"\\]){1,5000})"',
    r'"message"\s*:\s*"((?:\\.|[^"\\]){1,5000})"',
    r'"text"\s*:\s*"((?:\\.|[^"\\]){20,5000})"',
    r'"story"\s*:\s*"((?:\\.|[^"\\]){20,5000})"',
]

GENERIC_POST_CONTENT_EXACT = {
    "facebook",
    "log in",
    "log into facebook",
    "log in or sign up to view",
    "see posts, photos and more on facebook.",
    "see posts, photos and more on facebook",
}

GENERIC_POST_CONTENT_FRAGMENTS = (
    "server error field_exception",
    "check server logs for details",
    "unsupported browser",
    "browser isn't supported",
)

INVISIBLE_INPUT_CHARS_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFE0E\uFE0F]")
DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE: dict[str, float] = {}
DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT = ""


@dataclass(frozen=True)
class FetchResult:
    http_code: int
    text: str
    final_url: str
    reason: str


@dataclass(frozen=True)
class CookieCandidate:
    source: str
    cookies: dict[str, str]
    masked_id: str = ""

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookies)


def get_latest_post(
    resolved: ResolvedInput,
    request_cookies: Mapping[str, Any] | None = None,
    request_cookie_pool: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    uid = normalize_uid(resolved.uid)
    if not uid:
        return _empty_result(
            uid="",
            method="invalid_uid",
            reason="invalid_uid_or_uid_not_found",
            http_code=0,
        )

    timeout = _request_timeout()
    max_attempts = _max_probe_attempts()
    attempts: list[dict[str, Any]] = []
    best_failure: dict[str, Any] | None = None
    attempt_count = 0

    for candidate in build_cookie_candidates(request_cookies, request_cookie_pool):
        urls = build_facebook_latest_post_probe_urls(uid, resolved.username, candidate.has_cookie)
        queued_usernames = {str(resolved.username or "").strip().lower()} if resolved.username else set()
        headers_list = _headers_for_candidate(candidate)

        for url in urls:
            for header_label, headers in headers_list:
                attempt_count += 1
                fetch = _fetch_text(url, headers, timeout)
                discovered_username = extract_profile_username_from_url(fetch.final_url)
                if candidate.has_cookie and discovered_username and discovered_username.lower() not in queued_usernames:
                    queued_usernames.add(discovered_username.lower())
                    for extra_url in build_facebook_latest_post_probe_urls(uid, discovered_username, candidate.has_cookie):
                        if extra_url not in urls:
                            urls.append(extra_url)

                parsed = parse_latest_post_from_html(fetch.text)
                has_post = bool(parsed and is_latest_post_id_token(parsed.get("postId")))
                has_evidence = bool(has_post and has_latest_post_evidence_in_html(fetch.text, parsed.get("postId")))
                http_success = 200 <= fetch.http_code < 400

                attempt = _attempt_record(url, fetch, candidate, header_label)
                if has_post and has_evidence and http_success:
                    content = extract_latest_post_content_from_html(fetch.text, parsed["postId"])
                    if not candidate.has_cookie and not is_trusted_no_cookie_latest_post(parsed, content):
                        attempt["reason"] = f"latest_post_no_cookie_untrusted_http_{fetch.http_code or 0}"
                        attempts.append(attempt)
                        best_failure = choose_better_latest_post_result(best_failure, attempt)
                        if attempt_count >= max_attempts:
                            return _failure_from_attempt(uid, attempts, best_failure, "latest_post_probe_limit")
                        continue
                    attempt["reason"] = "ok"
                    attempts.append(attempt)
                    return {
                        "ok": True,
                        "uid": uid,
                        "postId": parsed["postId"],
                        "timestamp": parsed["timestamp"],
                        "link": build_latest_post_link(uid, parsed["postId"]),
                        "content": content,
                        "postContent": content,
                        "method": "with_cookie" if candidate.has_cookie else "no_cookie",
                        "reason": "ok",
                        "httpCode": fetch.http_code,
                        "probeUrl": url,
                        "finalUrl": fetch.final_url,
                        "cookieSource": candidate.source,
                        "cookieFallbackUsed": candidate.has_cookie,
                        "probeAttempts": attempts,
                    }

                fail_reason = build_latest_post_failure_reason(fetch.text, fetch.final_url, fetch.http_code)
                if has_post and not has_evidence and http_success:
                    fail_reason = f"latest_post_candidate_untrusted_http_{fetch.http_code or 0}"
                attempt["reason"] = fail_reason
                attempts.append(attempt)
                best_failure = choose_better_latest_post_result(best_failure, attempt)

                if attempt_count >= max_attempts:
                    return _failure_from_attempt(uid, attempts, best_failure, "latest_post_probe_limit")

    return _failure_from_attempt(uid, attempts, best_failure, "latest_post_not_found")


def get_latest_post_direct_from_input(
    input_raw: Any,
    request_cookies: Mapping[str, Any] | None = None,
    request_cookie_pool: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    cleaned_input = sanitize_latest_post_input(input_raw)
    probe_urls = build_direct_latest_post_probe_urls(cleaned_input)
    if not probe_urls:
        return _empty_result("", "direct_invalid_input", "invalid_facebook_link", 0)

    attempts: list[dict[str, Any]] = []
    best_failure: dict[str, Any] | None = None
    timeout = _request_timeout()

    direct_uid = extract_direct_uid_from_facebook_url(cleaned_input)
    direct_username = extract_profile_username_from_url(cleaned_input)
    cache_key = _direct_checkpost_cache_key(cleaned_input, direct_uid, direct_username)
    cookie_candidates = _prioritize_direct_cookie_candidates(
        [candidate for candidate in build_cookie_candidates(request_cookies, request_cookie_pool) if candidate.has_cookie]
    )
    direct_candidates: list[CookieCandidate] = []
    if not cookie_candidates or not _direct_checkpost_requires_cookie(cache_key):
        direct_candidates.append(CookieCandidate("no_cookie", {}))
    direct_candidates.extend(cookie_candidates)

    for candidate in direct_candidates:
        move_to_next_candidate = False
        for url in probe_urls:
            for header_label, headers in _headers_for_candidate(candidate):
                fetch = _fetch_text(url, headers, timeout)
                parsed = parse_latest_post_from_html(fetch.text)
                has_post = bool(parsed and is_latest_post_id_token(parsed.get("postId")))
                has_evidence = bool(has_post and has_latest_post_evidence_in_html(fetch.text, parsed.get("postId")))
                http_success = 200 <= fetch.http_code < 400
                attempt = _attempt_record(url, fetch, candidate, header_label)

                if has_post and has_evidence and http_success:
                    content = extract_latest_post_content_from_html(fetch.text, parsed["postId"])
                    if not candidate.has_cookie and not is_trusted_no_cookie_latest_post(parsed, content):
                        attempt["reason"] = f"latest_post_no_cookie_untrusted_http_{fetch.http_code or 0}"
                        attempts.append(attempt)
                        best_failure = choose_better_latest_post_result(best_failure, attempt)
                        continue

                    post_link = extract_facebook_post_url_from_html(fetch.text) or build_direct_latest_post_link(
                        cleaned_input,
                        parsed["postId"],
                        direct_uid,
                        direct_username,
                    )
                    attempt["reason"] = "ok"
                    attempts.append(attempt)
                    if candidate.has_cookie:
                        _remember_direct_checkpost_working_cookie(candidate)
                    return {
                        "ok": True,
                        "uid": direct_uid,
                        "username": direct_username,
                        "name": "",
                        "postId": parsed["postId"],
                        "timestamp": parsed["timestamp"],
                        "link": post_link,
                        "content": content,
                        "postContent": content,
                        "method": "direct_with_cookie" if candidate.has_cookie else "direct_no_cookie",
                        "source": "direct_link_scrape",
                        "reason": "ok",
                        "httpCode": fetch.http_code,
                        "probeUrl": url,
                        "finalUrl": fetch.final_url,
                        "cookieSource": candidate.source,
                        "cookieFallbackUsed": candidate.has_cookie,
                        "probeAttempts": attempts,
                        "directInput": cleaned_input,
                    }

                fail_reason = build_latest_post_failure_reason(fetch.text, fetch.final_url, fetch.http_code)
                if has_post and not has_evidence and http_success:
                    fail_reason = f"latest_post_candidate_untrusted_http_{fetch.http_code or 0}"
                attempt["reason"] = fail_reason
                attempts.append(attempt)
                best_failure = choose_better_latest_post_result(best_failure, attempt)
                if not candidate.has_cookie and _is_direct_no_cookie_terminal_reason(fail_reason):
                    _remember_direct_checkpost_requires_cookie(cache_key)
                    attempt["fastFallbackToCookie"] = True
                    move_to_next_candidate = True
                    break
            if move_to_next_candidate:
                break

    failure = _failure_from_attempt(direct_uid, attempts, best_failure, "direct_latest_post_not_found")
    failure["username"] = direct_username
    failure["name"] = ""
    failure["source"] = "direct_link_scrape"
    failure["directInput"] = cleaned_input
    return failure


def sanitize_latest_post_input(input_raw: Any) -> str:
    value = INVISIBLE_INPUT_CHARS_RE.sub("", str(input_raw or ""))
    value = value.replace("\u00A0", " ").strip()
    if value and not re.match(r"^[a-z][a-z0-9+.-]*://", value, flags=re.IGNORECASE):
        if "." in value or "/" in value:
            value = f"https://{value.lstrip('/')}"
    return value


def build_direct_latest_post_probe_urls(input_raw: Any) -> list[str]:
    value = sanitize_latest_post_input(input_raw)
    if not value:
        return []

    uid = normalize_uid(value)
    if uid:
        return [f"https://www.facebook.com/profile.php?id={quote(uid, safe='')}&sk=posts"]

    parsed = urlsplit(value)
    host = parsed.netloc.lower()
    if "facebook.com" not in host:
        return []

    path = parsed.path or "/"
    path_lower = path.lower()
    urls: list[str] = []
    if path_lower.endswith("/posts") or "/posts/" in path_lower or "story.php" in path_lower or "permalink.php" in path_lower:
        urls.append(urlunsplit((parsed.scheme or "https", parsed.netloc or "www.facebook.com", path, parsed.query, "")))

    if path_lower.strip("/") == "profile.php":
        query = parse_qs(parsed.query)
        uid_values = query.get("id") or []
        uid = normalize_uid(uid_values[0] if uid_values else "")
        if uid:
            urls.append(f"https://www.facebook.com/profile.php?id={quote(uid, safe='')}&sk=posts")
            urls.append(f"https://www.facebook.com/profile.php?id={quote(uid, safe='')}")
    else:
        base = urlunsplit((parsed.scheme or "https", parsed.netloc or "www.facebook.com", path, "", ""))
        urls.append(_with_query_param(base, "sk=posts"))
        urls.append(base)

    original = urlunsplit((parsed.scheme or "https", parsed.netloc or "www.facebook.com", path, parsed.query, ""))
    urls.append(original)
    return _unique(urls)


def extract_direct_uid_from_facebook_url(input_raw: Any) -> str:
    value = sanitize_latest_post_input(input_raw)
    uid = normalize_uid(value)
    if uid:
        return uid
    parsed = urlsplit(value)
    if "facebook.com" not in parsed.netloc.lower():
        return ""
    if parsed.path.strip("/").lower() == "profile.php":
        values = parse_qs(parsed.query).get("id") or []
        return normalize_uid(values[0] if values else "")
    first_segment = parsed.path.strip("/").split("/", 1)[0]
    return normalize_uid(first_segment)


def build_direct_latest_post_link(input_raw: Any, post_id_raw: Any, uid: str = "", username: str = "") -> str:
    post_id = str(post_id_raw or "").strip()
    if not post_id:
        return ""
    if uid:
        return build_latest_post_link(uid, post_id)
    if username:
        return f"https://www.facebook.com/{quote(username, safe='.')}/posts/{quote(post_id, safe='')}"

    parsed = urlsplit(sanitize_latest_post_input(input_raw))
    path = parsed.path.strip("/")
    first_segment = path.split("/", 1)[0].strip()
    if first_segment and first_segment.lower() != "profile.php":
        return f"https://www.facebook.com/{quote(first_segment, safe='.')}/posts/{quote(post_id, safe='')}"
    return ""


def _direct_checkpost_cache_key(input_raw: Any, uid: str = "", username: str = "") -> str:
    if uid:
        return f"uid:{uid}"
    if username:
        return f"username:{username.strip().lower()}"
    value = sanitize_latest_post_input(input_raw)
    if not value:
        return ""
    parsed = urlsplit(value)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query if path.lower().strip("/") == "profile.php" else ""
    return urlunsplit(("https", host, path, query, "")).lower()


def _direct_checkpost_requires_cookie(cache_key: str) -> bool:
    if not cache_key:
        return False
    expires_at = DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE.get(cache_key)
    if not expires_at:
        return False
    if expires_at <= time.time():
        DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE.pop(cache_key, None)
        return False
    return True


def _remember_direct_checkpost_requires_cookie(cache_key: str) -> None:
    if cache_key:
        DIRECT_CHECKPOST_REQUIRES_COOKIE_CACHE[cache_key] = time.time() + _direct_checkpost_requires_cookie_ttl()


def _direct_checkpost_requires_cookie_ttl() -> int:
    try:
        configured = int(os.getenv("CHECKPOST_REQUIRES_COOKIE_CACHE_TTL_SEC", "21600"))
    except ValueError:
        configured = 21600
    return max(300, min(configured, 86400))


def _prioritize_direct_cookie_candidates(candidates: list[CookieCandidate]) -> list[CookieCandidate]:
    preferred = DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT
    if not preferred:
        return candidates
    return sorted(candidates, key=lambda candidate: 0 if _cookie_fingerprint(candidate.cookies) == preferred else 1)


def _remember_direct_checkpost_working_cookie(candidate: CookieCandidate) -> None:
    global DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT
    fingerprint = _cookie_fingerprint(candidate.cookies)
    if fingerprint:
        DIRECT_CHECKPOST_PREFERRED_COOKIE_FINGERPRINT = fingerprint


def _is_direct_no_cookie_terminal_reason(reason_raw: Any) -> bool:
    reason = str(reason_raw or "")
    return (
        reason == "auth_wall"
        or reason == "checkpoint_detected"
        or reason == "profile_unavailable"
        or reason.startswith("timeline_shell_no_post_data")
        or reason.startswith("unsupported_browser_interstitial")
    )


def _with_query_param(url: str, query: str) -> str:
    parsed = urlsplit(url)
    merged = parsed.query
    if query not in merged:
        merged = f"{merged}&{query}" if merged else query
    return urlunsplit((parsed.scheme or "https", parsed.netloc or "www.facebook.com", parsed.path or "/", merged, ""))


def build_facebook_latest_post_probe_urls(uid: str, username: str = "", with_cookie: bool = False) -> list[str]:
    normalized_uid = normalize_uid(uid)
    if not normalized_uid:
        return []

    urls: list[str] = []
    safe_username = quote(str(username or "").strip().strip("/"), safe=".") if username else ""

    if with_cookie:
        if safe_username:
            urls.extend(
                [
                    f"https://www.facebook.com/{safe_username}?sk=posts",
                    f"https://www.facebook.com/{safe_username}",
                ]
            )
        urls.extend(
            [
                f"https://www.facebook.com/profile.php?id={normalized_uid}",
            ]
        )
        return _unique(urls)

    if safe_username:
        urls.extend(
            [
                f"https://www.facebook.com/{safe_username}?sk=posts",
                f"https://www.facebook.com/{safe_username}",
            ]
        )
    urls.append(f"https://www.facebook.com/profile.php?id={normalized_uid}")

    return _unique(urls)


def extract_profile_username_from_url(url_raw: Any) -> str:
    url = normalize_facebook_payload_text(url_raw)
    if not url:
        return ""

    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    if "facebook.com" not in host:
        return ""

    path = parsed.path.strip("/")
    first_segment = path.split("/", 1)[0].strip()
    if first_segment in {"login", "checkpoint"}:
        next_values = parse_qs(parsed.query).get("next") or []
        for next_url in next_values:
            username = extract_profile_username_from_url(unquote(next_url))
            if username:
                return username
        return ""

    if not first_segment:
        return ""
    lowered = first_segment.lower()
    if lowered in {
        "profile.php",
        "people",
        "share",
        "story.php",
        "permalink.php",
        "photo.php",
        "watch",
        "groups",
        "pages",
    }:
        return ""
    if re.fullmatch(r"\d{5,20}", first_segment):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9.]{3,80}", first_segment):
        return ""
    return first_segment


def parse_latest_post_from_html(html_raw: Any) -> dict[str, Any] | None:
    html = normalize_facebook_payload_text(html_raw)
    if not html:
        return None

    post_id = ""
    timestamp = 0

    for pattern in LATEST_POST_PAIR_PATTERNS:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        post_id = str(match.group(1) or "").strip()
        timestamp = normalize_unix_timestamp_seconds(match.group(2))
        break

    if not post_id:
        for pattern in LATEST_POST_ID_PATTERNS:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if not match:
                continue
            post_id = str(match.group(1) or "").strip()
            break

    if not post_id:
        post_url = extract_facebook_post_url_from_html(html)
        post_id = extract_facebook_post_id_from_url(post_url)

    if not timestamp:
        for pattern in LATEST_POST_TIME_PATTERNS:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if not match:
                continue
            timestamp = normalize_unix_timestamp_seconds(match.group(1))
            break

    if not is_latest_post_id_token(post_id):
        return None

    return {"postId": post_id, "timestamp": timestamp}


def extract_latest_post_content_from_html(html_raw: Any, post_id_raw: Any = "") -> str:
    html = str(html_raw or "")
    if not html:
        return ""

    normalized = normalize_facebook_payload_text(html)
    post_id = str(post_id_raw or "").strip()
    windows: list[str] = []

    if post_id:
        for match in re.finditer(re.escape(post_id), normalized, flags=re.IGNORECASE):
            start = max(0, match.start() - 4000)
            end = min(len(normalized), match.end() + 9000)
            windows.append(normalized[start:end])
            if len(windows) >= 4:
                break

    windows.append(normalized[:60000])

    for window in windows:
        for candidate in extract_json_content_candidates_from_text(window):
            if candidate:
                return candidate

    for candidate in (
        extract_meta_content_from_html(html, "property", "og:description"),
        extract_meta_content_from_html(html, "name", "description"),
        extract_meta_content_from_html(html, "property", "twitter:description"),
    ):
        cleaned = clean_facebook_post_content(candidate)
        if cleaned:
            return cleaned

    return ""


def extract_json_content_candidates_from_text(text_raw: Any) -> list[str]:
    text = str(text_raw or "")
    if not text:
        return []

    candidates: list[str] = []
    for pattern in POST_CONTENT_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            cleaned = clean_facebook_post_content(decode_facebook_json_text(match.group(1)))
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
            if len(candidates) >= 12:
                return candidates
    return candidates


def is_trusted_no_cookie_latest_post(parsed_raw: Any, content_raw: Any) -> bool:
    parsed = parsed_raw if isinstance(parsed_raw, Mapping) else {}
    timestamp = normalize_unix_timestamp_seconds(parsed.get("timestamp"))
    if timestamp:
        return True
    content = clean_facebook_post_content(content_raw)
    if not content:
        return False
    lowered = content.lower()
    return not any(fragment in lowered for fragment in GENERIC_POST_CONTENT_FRAGMENTS)


def has_latest_post_evidence_in_html(html_raw: Any, post_id_raw: Any) -> bool:
    html = normalize_facebook_payload_text(html_raw)
    post_id = str(post_id_raw or "").strip()
    if not html or not post_id:
        return False

    escaped_post_id = re.escape(post_id)
    patterns = [
        rf'"post_id"\s*:\s*"?{escaped_post_id}"?',
        rf'"top_level_post_id"\s*:\s*"?{escaped_post_id}"?',
        rf'"story_fbid"\s*:\s*"?{escaped_post_id}"?',
        rf'"legacy_fbid"\s*:\s*"?{escaped_post_id}"?',
        rf"(?:^|[?&]|%3f|%26)story_fbid(?:=|%3d){escaped_post_id}(?:\b|[&#%])",
        rf"(?:^|[?&]|%3f|%26)fbid(?:=|%3d){escaped_post_id}(?:\b|[&#%])",
        rf"/posts/{escaped_post_id}(?:\b|[/?#])",
    ]
    return any(re.search(pattern, html, flags=re.IGNORECASE) for pattern in patterns)


def build_latest_post_failure_reason(body_raw: Any, final_url_raw: Any, http_code_raw: Any) -> str:
    body = str(body_raw or "")
    body_low = body.lower()
    final_url = str(final_url_raw or "")
    http_code = int(http_code_raw or 0)

    if "checkpoint" in body_low or "/checkpoint/" in final_url.lower():
        return "checkpoint_detected"
    if _is_auth_wall(body_low, final_url):
        return "auth_wall"
    if _contains_profile_unavailable(body_low):
        return "profile_unavailable"
    if (
        "unsupported-interstitial" in body_low
        or "browser_unsupported" in body_low
        or "this browser isn't supported" in body_low
        or "this browser is not supported" in body_low
        or "weblite_unsupported" in body_low
    ):
        return f"unsupported_browser_interstitial_http_{http_code or 0}"
    if (
        "sorry, something went wrong" in body_low
        or "we're working on getting this fixed as soon as we can" in body_low
        or "<title>error</title>" in body_low
    ):
        return f"facebook_error_page_http_{http_code or 0}"
    if http_code == 200 and _has_weblite_shell_without_post_marker(body_low):
        return "timeline_shell_no_post_data_http_200"
    if http_code:
        return f"latest_post_not_found_http_{http_code}"
    return "latest_post_not_found"


def build_cookie_candidates(
    request_cookies: Mapping[str, Any] | None = None,
    request_cookie_pool: list[Mapping[str, Any]] | None = None,
) -> list[CookieCandidate]:
    candidates: list[CookieCandidate] = []
    seen: set[str] = set()

    request_cookie = _normalize_cookie_dict(request_cookies)
    if request_cookie:
        seen.add(_cookie_fingerprint(request_cookie))
        candidates.append(CookieCandidate("request_cookie", request_cookie, _masked_cookie_dict(request_cookie)))

    for index, item in enumerate(request_cookie_pool or [], start=1):
        cookies = _normalize_cookie_dict(item)
        fingerprint = _cookie_fingerprint(cookies)
        if cookies and fingerprint not in seen:
            seen.add(fingerprint)
            candidates.append(CookieCandidate(f"request_pool_{index}", cookies, _masked_cookie_dict(cookies)))

    for account in _local_cookie_accounts():
        fingerprint = _cookie_fingerprint(account.cookies)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append(CookieCandidate(account.source, dict(account.cookies), account.masked_id))

    candidates.append(CookieCandidate("no_cookie", {}))
    return candidates


def normalize_facebook_payload_text(raw: Any) -> str:
    normalized = (
        str(raw or "")
        .replace("\\/", "/")
        .replace("\\u002f", "/")
        .replace("\\u003a", ":")
        .replace("\\u003d", "=")
        .replace("\\u0026", "&")
        .replace("\\u003f", "?")
        .replace("\\x2f", "/")
        .replace("\\x3a", ":")
        .replace("\\x3d", "=")
        .replace("\\x26", "&")
        .replace("\\x3f", "?")
        .replace("&#x2f;", "/")
        .replace("&#x3a;", ":")
        .replace("&#x3d;", "=")
        .replace("&#x26;", "&")
        .replace("&#x3f;", "?")
        .replace("&#47;", "/")
        .replace("&#58;", ":")
        .replace("&#61;", "=")
        .replace("&#38;", "&")
        .replace("&#63;", "?")
        .replace("&amp;", "&")
        .replace("%253d", "%3d")
        .replace("%253D", "%3D")
        .replace("%2526", "%26")
        .replace("%253f", "%3f")
        .replace("%253F", "%3F")
        .replace("%3d", "=")
        .replace("%3D", "=")
        .replace("%26", "&")
        .replace("%3f", "?")
        .replace("%3F", "?")
        .replace("&quot;", '"')
    )
    return safe_percent_decode_text(normalized, 2)


def safe_percent_decode_text(value_raw: Any, rounds_raw: int = 1) -> str:
    value = str(value_raw or "")
    if not value:
        return ""
    rounds = max(1, min(3, int(rounds_raw or 1)))
    for _ in range(rounds):
        next_value = re.sub(r"%([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
        if next_value == value:
            break
        value = next_value
    return value


def normalize_unix_timestamp_seconds(timestamp_raw: Any) -> int:
    try:
        timestamp = int(float(timestamp_raw or 0))
    except Exception:
        timestamp = 0
    if timestamp > 1000000000000:
        timestamp = timestamp // 1000
    return max(0, timestamp)


def is_story_fbid_token(value_raw: Any) -> bool:
    return bool(re.fullmatch(r"pfbid[a-zA-Z0-9_]+", str(value_raw or "").strip()))


def is_latest_post_id_token(value_raw: Any) -> bool:
    value = str(value_raw or "").strip()
    if not value:
        return False
    if re.fullmatch(r"\d{8,}", value):
        return True
    return is_story_fbid_token(value)


def build_latest_post_link(uid_raw: Any, post_id_raw: Any) -> str:
    uid = str(uid_raw or "").strip()
    post_id = str(post_id_raw or "").strip()
    if not uid or not post_id:
        return ""
    if is_story_fbid_token(post_id):
        return f"https://www.facebook.com/permalink.php?story_fbid={quote(post_id, safe='')}&id={quote(uid, safe='')}"
    return f"https://www.facebook.com/{uid}/posts/{post_id}"


def extract_facebook_post_id_from_url(url_raw: Any) -> str:
    url = str(url_raw or "").strip()
    if not url:
        return ""
    for pattern in (
        r"(?:^|[?&])story_fbid=([A-Za-z0-9_]{8,})",
        r"(?:^|[?&])fbid=(\d{8,})",
        r"/posts/([A-Za-z0-9_]{8,})",
    ):
        match = re.search(pattern, url, flags=re.IGNORECASE)
        if not match:
            continue
        post_id = str(match.group(1) or "").strip()
        if is_latest_post_id_token(post_id):
            return post_id
    return ""


def extract_facebook_post_url_from_html(html_raw: Any) -> str:
    html = str(html_raw or "")
    if not html:
        return ""
    patterns = [
        r"https?://(?:www|m|mbasic)\.facebook\.com/(?:story\.php|permalink\.php)[^\"'\s<>]{0,700}",
        r"https?://(?:www|m|mbasic)\.facebook\.com/[^/\"'\s<>?#]+/posts/[A-Za-z0-9_]{8,}[^\"'\s<>]{0,500}",
        r"/(?:story\.php|permalink\.php)[^\"'\s<>]{0,700}",
        r"/[^/\"'\s<>?#]+/posts/[A-Za-z0-9_]{8,}[^\"'\s<>]{0,500}",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        raw = str(match.group(0) or "").strip()
        normalized = raw if raw.lower().startswith("http") else f"https://www.facebook.com{raw}"
        if extract_facebook_post_id_from_url(normalized):
            return normalized
    return ""


def decode_facebook_json_text(value_raw: Any) -> str:
    value = str(value_raw or "")
    if not value:
        return ""
    try:
        return str(json.loads(f'"{value}"'))
    except Exception:
        return value.replace("\\/", "/").replace('\\"', '"').replace("\\n", "\n").replace("\\r", "\n").replace("\\t", " ")


def clean_facebook_post_content(value_raw: Any) -> str:
    if value_raw is None or isinstance(value_raw, (dict, list, tuple, set)):
        return ""
    text = html_lib.unescape(str(value_raw or ""))
    if not text:
        return ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return ""

    lowered = text.lower()
    if lowered in GENERIC_POST_CONTENT_EXACT:
        return ""
    generic_prefixes = (
        "log in or sign up to view",
        "see posts, photos and more on facebook",
        "you must log in",
    )
    if len(text) < 180 and any(lowered.startswith(item) for item in generic_prefixes):
        return ""
    return text


def extract_meta_content_from_html(html_raw: Any, attr_name: str, attr_value: str) -> str:
    html = str(html_raw or "")
    if not html:
        return ""
    escaped_name = re.escape(attr_name)
    escaped_value = re.escape(attr_value)
    patterns = [
        rf'<meta[^>]+\b{escaped_name}=["\']{escaped_value}["\'][^>]+\bcontent=["\']([^"\']*)["\'][^>]*>',
        rf'<meta[^>]+\bcontent=["\']([^"\']*)["\'][^>]+\b{escaped_name}=["\']{escaped_value}["\'][^>]*>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return clean_facebook_post_content(match.group(1))
    return ""


def choose_better_latest_post_result(current_raw: Any, candidate_raw: Any) -> dict[str, Any] | None:
    current = current_raw if isinstance(current_raw, dict) else None
    candidate = candidate_raw if isinstance(candidate_raw, dict) else None
    if current is None:
        return candidate
    if candidate is None:
        return current
    current_score = latest_post_failure_priority(current.get("reason"), current.get("httpCode"))
    candidate_score = latest_post_failure_priority(candidate.get("reason"), candidate.get("httpCode"))
    if candidate_score > current_score:
        return candidate
    if candidate_score < current_score:
        return current
    if not int(current.get("httpCode") or 0) and int(candidate.get("httpCode") or 0):
        return candidate
    return current


def latest_post_failure_priority(reason_raw: Any, http_code_raw: Any) -> int:
    reason = str(reason_raw or "").lower()
    http_code = int(http_code_raw or 0)
    if not reason:
        return 0
    if reason.startswith("checkpoint"):
        return 5000
    if reason.startswith("profile_unavailable"):
        return 4500
    if reason.startswith("unsupported_browser_interstitial"):
        return 4400
    if reason.startswith("facebook_error_page"):
        return 4300
    if reason.startswith("timeline_shell_no_post_data"):
        return 4200
    if reason.startswith("latest_post_not_found"):
        return 4000 if http_code in (200, 404) else 3500
    if reason.startswith("auth_wall"):
        return 3000
    if reason.startswith("request_error"):
        return 2000
    return 1000


def _fetch_text(url: str, headers: Mapping[str, str], timeout: float) -> FetchResult:
    try:
        response = requests.get(url, headers=dict(headers), timeout=timeout, allow_redirects=True)
        return FetchResult(
            http_code=response.status_code,
            text=response.text or "",
            final_url=response.url or url,
            reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
        )
    except requests.RequestException as exc:
        return FetchResult(0, "", url, f"request_error:{type(exc).__name__}")


def _headers_for_candidate(candidate: CookieCandidate) -> list[tuple[str, dict[str, str]]]:
    if not candidate.has_cookie:
        return [("no_cookie_desktop", _base_headers(_desktop_user_agent()))]
    return [("cookie_desktop", _cookie_headers(candidate.cookies, _desktop_user_agent()))]


def _base_headers(user_agent: str) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Referer": "https://www.facebook.com/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    return headers


def _cookie_headers(cookies: Mapping[str, str], user_agent: str) -> dict[str, str]:
    headers = _base_headers(user_agent)
    headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items() if key and value)
    return headers


def _desktop_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )


def _mobile_user_agent() -> str:
    return (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
    )


def _attempt_record(
    url: str,
    fetch: FetchResult,
    candidate: CookieCandidate,
    header_label: str,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "url": url,
        "httpCode": fetch.http_code,
        "reason": fetch.reason,
        "finalUrl": fetch.final_url,
        "method": "with_cookie" if candidate.has_cookie else "no_cookie",
        "cookieSource": candidate.source,
        "header": header_label,
    }
    if candidate.masked_id:
        item["cookieAccount"] = candidate.masked_id
    return item


def _failure_from_attempt(
    uid: str,
    attempts: list[dict[str, Any]],
    best_failure: dict[str, Any] | None,
    fallback_reason: str,
) -> dict[str, Any]:
    best = best_failure or {}
    return {
        "ok": False,
        "uid": uid,
        "postId": "",
        "timestamp": 0,
        "link": "",
        "content": "",
        "postContent": "",
        "method": str(best.get("method") or "no_cookie"),
        "reason": str(best.get("reason") or fallback_reason),
        "httpCode": int(best.get("httpCode") or 0),
        "probeUrl": str(best.get("url") or ""),
        "finalUrl": str(best.get("finalUrl") or ""),
        "cookieFallbackUsed": any(str(item.get("method")) == "with_cookie" for item in attempts),
        "probeAttempts": attempts,
    }


def _empty_result(uid: str, method: str, reason: str, http_code: int) -> dict[str, Any]:
    return {
        "ok": False,
        "uid": uid,
        "postId": "",
        "timestamp": 0,
        "link": "",
        "content": "",
        "postContent": "",
        "method": method,
        "reason": reason,
        "httpCode": http_code,
        "probeUrl": "",
        "finalUrl": "",
        "cookieFallbackUsed": False,
        "probeAttempts": [],
    }


def _local_cookie_accounts() -> list[CookieAccount]:
    limit = _cookie_account_limit()
    return [account for account in load_cookie_accounts() if account.is_usable][:limit]


def _normalize_cookie_dict(raw: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key or "").strip() and str(value or "").strip()
    }


def _masked_cookie_dict(cookies: Mapping[str, str]) -> str:
    c_user = str(cookies.get("c_user", "") or "")
    if len(c_user) <= 6:
        return "***" if c_user else ""
    return f"{c_user[:4]}***{c_user[-4:]}"


def _cookie_fingerprint(cookies: Mapping[str, str]) -> str:
    if not cookies:
        return ""
    return "|".join(f"{key}={cookies[key]}" for key in sorted(cookies.keys()))


def _request_timeout() -> float:
    try:
        configured = float(os.getenv("LATEST_POST_REQUEST_TIMEOUT", "7"))
    except ValueError:
        configured = 7.0
    return max(4.0, min(configured, max(4.0, get_config().request_timeout_seconds)))


def _max_probe_attempts() -> int:
    try:
        return max(4, int(os.getenv("LATEST_POST_MAX_PROBE_ATTEMPTS", "18")))
    except ValueError:
        return 18


def _cookie_account_limit() -> int:
    try:
        return max(0, int(os.getenv("LATEST_POST_COOKIE_ACCOUNT_LIMIT", "5")))
    except ValueError:
        return 5


def _is_auth_wall(body_low: str, final_url: str) -> bool:
    return (
        "login_form" in body_low
        or "log in or sign up" in body_low
        or "you must log in" in body_low
        or "/login" in final_url.lower()
        or "/recover" in final_url.lower()
        or "/security" in final_url.lower()
        or "/accounts" in final_url.lower()
    )


def _contains_profile_unavailable(body_low: str) -> bool:
    return (
        "content isn't available" in body_low
        or "this content isn't available" in body_low
        or "page isn't available" in body_low
        or "this page isn't available" in body_low
    )


def _has_weblite_shell_without_post_marker(body_low: str) -> bool:
    has_shell = (
        "window.weblitebootloader" in body_low
        or "appautostartdisabled" in body_low
        or "pipe_no_www_response" in body_low
    )
    has_post_marker = (
        "story_fbid" in body_low
        or "/posts/" in body_low
        or "permalink.php" in body_low
        or "post_id" in body_low
        or "legacy_fbid" in body_low
    )
    return has_shell and not has_post_marker


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
