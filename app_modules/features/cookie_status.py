from __future__ import annotations

from time import perf_counter
from typing import Any
import re

import requests

from app_modules.resolvers.facebook_cookies import CookieAccount, cookie_header, load_cookie_accounts


COOKIE_STATUS_PROBE_URL = "https://mbasic.facebook.com/notifications.php"


def get_cookie_status() -> dict[str, Any]:
    started = perf_counter()
    accounts = load_cookie_accounts()
    rows = [probe_cookie_account(index, account) for index, account in enumerate(accounts, start=1)]
    live_count = sum(1 for row in rows if row["status"] == "LIVE")
    usable_count = sum(1 for row in rows if row["usable"])
    return {
        "ok": True,
        "total": len(rows),
        "usable": usable_count,
        "live": live_count,
        "dead": len(rows) - live_count,
        "accounts": rows,
        "source": "facebook_cookie_probe",
        "reason": "ok",
        "elapsedMs": int((perf_counter() - started) * 1000),
    }


def probe_cookie_account(index: int, account: CookieAccount) -> dict[str, Any]:
    if not account.is_usable:
        return {
            "index": index,
            "cUser": account.masked_id,
            "usable": False,
            "status": "UNUSABLE",
            "reason": "missing_c_user_or_xs",
            "httpCode": 0,
            "elapsedMs": 0,
        }

    started = perf_counter()
    try:
        response = requests.get(
            COOKIE_STATUS_PROBE_URL,
            headers={
                "User-Agent": account.browser_user_agent or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/134.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "Cache-Control": "max-age=0",
                "Cookie": cookie_header(account),
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=(4, 10),
            allow_redirects=True,
        )
        elapsed_ms = int((perf_counter() - started) * 1000)
        status, reason = classify_cookie_response(account, response.url, response.text or "")
        return {
            "index": index,
            "cUser": account.masked_id,
            "usable": True,
            "status": status,
            "reason": reason,
            "httpCode": int(response.status_code),
            "elapsedMs": elapsed_ms,
        }
    except requests.RequestException as exc:
        return {
            "index": index,
            "cUser": account.masked_id,
            "usable": True,
            "status": "UNKNOWN",
            "reason": f"request_error:{exc.__class__.__name__}",
            "httpCode": 0,
            "elapsedMs": int((perf_counter() - started) * 1000),
        }


def classify_cookie_response(account: CookieAccount, final_url: str, body: str) -> tuple[str, str]:
    haystack = f"{final_url}\n{body[:80000]}".lower()
    if "checkpoint" in haystack or "confirm your identity" in haystack:
        return "CHECKPOINT", "checkpoint_detected"

    login_markers = (
        "/login",
        "login_form",
        "log in to facebook",
        "dang nhap facebook",
    )
    viewer_ids = set(re.findall(
        r'"(?:actorid|actor_id|user_id|userid|viewerid)"\s*:\s*"?(\d+)',
        body[:80000],
        flags=re.IGNORECASE,
    ))
    actor_matches = bool(account.c_user and account.c_user in viewer_ids)
    explicit_logout = (
        'id="mbasic_logout_button"' in haystack
        or "href=\"/logout.php" in haystack
        or "href='/logout.php" in haystack
    )
    csrf_session = "fb_dtsg" in haystack and bool(account.c_user and account.c_user in body[:80000])
    logged_in = actor_matches or explicit_logout or csrf_session
    login_wall = any(marker in haystack for marker in login_markers)

    if logged_in:
        reason = "viewer_id_matches_cookie" if actor_matches else "explicit_logout_marker_found"
        return "LIVE", reason
    if login_wall:
        return "EXPIRED_OR_LOGIN", "redirected_to_login"
    return "UNKNOWN", "no_login_or_logged_in_marker"
