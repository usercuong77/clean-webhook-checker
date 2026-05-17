from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


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

DEFAULT_LOCAL_COOKIE_FILE = Path(__file__).resolve().parents[2] / "local_secrets" / "facebook_cookies.txt"


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


def load_cookie_accounts(
    path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[CookieAccount]:
    environ = os.environ if env is None else env
    explicit_path = _first_env_value(environ, COOKIE_FILE_ENV_KEYS)

    if path or explicit_path:
        candidate_path = Path(path or explicit_path)
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


def cookie_header(account: CookieAccount) -> str:
    parts = []
    for key, value in account.cookies.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
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
        cookies = {
            str(key).strip(): str(value).strip()
            for key, value in item.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        c_user = cookies.get("c_user", "")
        if not c_user:
            continue
        accounts.append(CookieAccount(c_user=c_user, source=source, index=index, cookies=cookies))
    return accounts


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
