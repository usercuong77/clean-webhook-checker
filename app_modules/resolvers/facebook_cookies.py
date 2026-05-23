from __future__ import annotations

import json
import os
import time
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse, urlunparse

import requests


COOKIE_FILE_ENV_KEYS = (
    "FACEBOOK_COOKIE_FILE",
    "UID_CHECKER_FB_COOKIE_FILE",
)

COOKIE_JSON_ENV_KEYS = (
    "UID_CHECKER_FB_COOKIES_JSON",
    "UID_CHECKER_FB_COOKIES_POOL_JSON",
    "FB_COOKIES_JSON",
    "FB_COOKIES_POOL_JSON",
)

REMOTE_COOKIE_POOL_URL_KEYS = (
    "UID_CHECKER_REMOTE_COOKIE_POOL_URL",
    "CLOUDFLARE_COOKIE_POOL_URL",
    "COOKIE_POOL_URL",
)

REMOTE_COOKIE_POOL_SECRET_KEYS = (
    "UID_CHECKER_REMOTE_COOKIE_POOL_SECRET",
    "COOKIE_POOL_SECRET",
    "RENDER_REGISTRATION_SECRET",
    "UID_CHECKER_API_KEY",
    "SMM_API_KEY",
    "VIPLIKE_SMM_API_KEY",
)

DEFAULT_LOCAL_COOKIE_FILE = Path(__file__).resolve().parents[2] / "local_secrets" / "facebook_cookies.txt"
DEFAULT_REMOTE_COOKIE_POOL_URL = (
    "https://clean-telegram-cloudflare-gateway.0987654321ct0987654321.workers.dev/admin/cookies/pool"
)

COOKIE_METADATA_KEYS = {"__user_agent", "_user_agent", "useragent"}
_REMOTE_CACHE: dict[str, Any] = {"expires_at": 0.0, "accounts": []}


@dataclass(frozen=True)
class CookieAccount:
    c_user: str
    source: str
    index: int
    cookies: dict[str, str] = field(repr=False)

    @property
    def is_usable(self) -> bool:
        return bool(self.c_user and self.cookies.get("xs"))

    @property
    def masked_id(self) -> str:
        value = str(self.c_user or "")
        if len(value) <= 6:
            return "***"
        return f"{value[:4]}***{value[-4:]}"

    @property
    def browser_user_agent(self) -> str:
        return _extract_browser_user_agent(self.cookies)


def load_cookie_accounts(
    path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[CookieAccount]:
    environ = os.environ if env is None else env

    if path:
        candidate_path = Path(path)
        if candidate_path.is_file():
            return _accounts_from_payload(_read_json_file(candidate_path), str(candidate_path))

    remote_accounts = _load_remote_cookie_accounts(environ)
    if remote_accounts:
        return remote_accounts

    explicit_path = _first_env_value(environ, COOKIE_FILE_ENV_KEYS)
    if explicit_path:
        candidate_path = Path(explicit_path)
        if candidate_path.is_file():
            return _accounts_from_payload(_read_json_file(candidate_path), str(candidate_path))

    for key in COOKIE_JSON_ENV_KEYS:
        raw_value = str(environ.get(key, "") or "").strip()
        if not raw_value:
            continue
        accounts = _accounts_from_payload(_parse_json(raw_value), key)
        if accounts:
            return accounts

    field_account = _account_from_individual_env(environ)
    if field_account:
        return [field_account]

    if env is None and DEFAULT_LOCAL_COOKIE_FILE.is_file():
        candidate_path = DEFAULT_LOCAL_COOKIE_FILE
        return _accounts_from_payload(_read_json_file(candidate_path), str(candidate_path))

    return []


def reload_cookie_accounts_cache() -> None:
    _REMOTE_CACHE["expires_at"] = 0.0
    _REMOTE_CACHE["accounts"] = []


def cookie_header(account: CookieAccount) -> str:
    parts = []
    for key, value in account.cookies.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if _is_cookie_metadata_key(clean_key):
            continue
        if clean_key and clean_value:
            parts.append(f"{clean_key}={clean_value}")
    return "; ".join(parts)


def masked_accounts(accounts: list[CookieAccount]) -> list[dict[str, Any]]:
    return [
        {
            "source": account.source,
            "index": account.index,
            "cUser": account.masked_id,
            "usable": account.is_usable,
            "cookieKeys": sorted(account.cookies.keys()),
        }
        for account in accounts
    ]


def _accounts_from_payload(payload: Any, source: str) -> list[CookieAccount]:
    items = _normalize_cookie_payload(payload)
    accounts: list[CookieAccount] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        cookies = _normalize_cookie_map(item)
        c_user = cookies.get("c_user", "")
        if not c_user:
            continue
        accounts.append(CookieAccount(c_user=c_user, source=source, index=index, cookies=cookies))
    return accounts


def _normalize_cookie_map(item: Mapping[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    browser_user_agent = ""
    for key, value in item.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not clean_key or not clean_value:
            continue
        lower_key = clean_key.lower()
        if lower_key == "useragent":
            browser_user_agent = _decode_user_agent_value(clean_value) or browser_user_agent
            continue
        if lower_key in {"__user_agent", "_user_agent"}:
            browser_user_agent = clean_value or browser_user_agent
            continue
        if lower_key == "_uafec" and not browser_user_agent:
            browser_user_agent = unquote(clean_value)
        cookies[clean_key] = clean_value

    if _looks_like_browser_user_agent(browser_user_agent):
        cookies["__user_agent"] = browser_user_agent
    return cookies


def _normalize_cookie_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("cookies"), list):
            return payload["cookies"]
        if isinstance(payload.get("accounts"), list):
            return payload["accounts"]
        return [payload]
    return []


def _read_json_file(path: Path) -> Any:
    try:
        return _parse_json(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except OSError:
        return None


def _parse_json(raw_value: str) -> Any:
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None


def _account_from_individual_env(environ: Mapping[str, str]) -> CookieAccount | None:
    cookies = {
        "c_user": str(environ.get("UID_CHECKER_FB_C_USER", "") or "").strip(),
        "xs": str(environ.get("UID_CHECKER_FB_XS", "") or "").strip(),
        "datr": str(environ.get("UID_CHECKER_FB_DATR", "") or "").strip(),
        "fr": str(environ.get("UID_CHECKER_FB_FR", "") or "").strip(),
        "sb": str(environ.get("UID_CHECKER_FB_SB", "") or "").strip(),
    }
    cookies = {key: value for key, value in cookies.items() if value}
    c_user = cookies.get("c_user", "")
    if not c_user:
        return None
    return CookieAccount(c_user=c_user, source="individual_env", index=0, cookies=cookies)


def _first_env_value(environ: Mapping[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(environ.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _load_remote_cookie_accounts(environ: Mapping[str, str]) -> list[CookieAccount]:
    if _is_truthy(environ.get("UID_CHECKER_REMOTE_COOKIE_DISABLED")):
        return []

    url = _remote_cookie_pool_url(environ)
    secret = _first_env_value(environ, REMOTE_COOKIE_POOL_SECRET_KEYS)
    if not url or not secret:
        return []

    now = time.time()
    cached_accounts = list(_REMOTE_CACHE.get("accounts") or [])
    if cached_accounts and float(_REMOTE_CACHE.get("expires_at") or 0) > now:
        return cached_accounts

    try:
        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "X-Render-Registration-Secret": secret,
                "X-Cookie-Pool-Secret": secret,
            },
            timeout=(2, _remote_timeout(environ)),
        )
        if response.status_code != 200:
            return cached_accounts
        payload = response.json()
    except (requests.RequestException, ValueError):
        return cached_accounts

    accounts = _accounts_from_payload(payload.get("accounts"), "remote_cookie_pool")
    if accounts:
        _REMOTE_CACHE["accounts"] = accounts
        _REMOTE_CACHE["expires_at"] = now + _remote_ttl(environ)
        return list(accounts)

    return cached_accounts


def _remote_cookie_pool_url(environ: Mapping[str, str]) -> str:
    explicit = _first_env_value(environ, REMOTE_COOKIE_POOL_URL_KEYS)
    if explicit:
        return explicit

    register_url = str(environ.get("CLOUDFLARE_RENDER_REGISTER_URL", "") or "").strip()
    if not register_url:
        return DEFAULT_REMOTE_COOKIE_POOL_URL

    try:
        parsed = urlparse(register_url)
        return urlunparse((parsed.scheme, parsed.netloc, "/admin/cookies/pool", "", "", ""))
    except ValueError:
        return ""


def _remote_ttl(environ: Mapping[str, str]) -> int:
    try:
        value = int(str(environ.get("UID_CHECKER_REMOTE_COOKIE_TTL_SEC", "") or "").strip())
        return max(10, min(value, 1800))
    except ValueError:
        return 120


def _remote_timeout(environ: Mapping[str, str]) -> float:
    try:
        value = float(str(environ.get("UID_CHECKER_REMOTE_COOKIE_TIMEOUT_SEC", "") or "").strip())
        return max(1.0, min(value, 10.0))
    except ValueError:
        return 5.0


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_cookie_metadata_key(key: str) -> bool:
    return str(key or "").strip().lower() in COOKIE_METADATA_KEYS


def _extract_browser_user_agent(cookies: Mapping[str, str]) -> str:
    for key in ("__user_agent", "_user_agent"):
        value = str(cookies.get(key, "") or "").strip()
        if _looks_like_browser_user_agent(value):
            return value

    raw_user_agent = str(cookies.get("useragent", "") or "").strip()
    decoded = _decode_user_agent_value(raw_user_agent)
    if decoded:
        return decoded

    uafec = str(cookies.get("_uafec", "") or "").strip()
    decoded_uafec = unquote(uafec)
    return decoded_uafec if _looks_like_browser_user_agent(decoded_uafec) else ""


def _decode_user_agent_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    decoded_uri = unquote(raw)
    if _looks_like_browser_user_agent(decoded_uri):
        return decoded_uri
    try:
        normalized = raw.replace("-", "+").replace("_", "/")
        decoded = base64.b64decode(normalized + "=" * (-len(normalized) % 4)).decode("utf-8", errors="ignore")
        if _looks_like_browser_user_agent(decoded):
            return decoded
    except (ValueError, OSError):
        return ""
    return raw if _looks_like_browser_user_agent(raw) else ""


def _looks_like_browser_user_agent(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized.lower().startswith("mozilla/5.0"):
        return False
    return any(marker in normalized.lower() for marker in ("chrome", "safari", "firefox", "edg", "mobile"))
