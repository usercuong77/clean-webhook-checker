from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

from app_modules.core.config import get_config


SECRET_QUERY_KEYS = ("secret", "webhook_secret", "webhookSecret", "token")


def build_relay_target_url() -> str:
    config = get_config()
    target_url = config.telegram_relay_target_url
    shared_secret = config.webhook_shared_secret
    if not target_url or not shared_secret:
        return target_url

    parsed = urlsplit(target_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query_has_webhook_secret(query):
        return target_url

    pairs: list[tuple[str, str]] = []
    for key, values in query.items():
        for value in values:
            pairs.append((key, value))
    pairs.append(("secret", shared_secret))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment))


def relay_status() -> dict[str, Any]:
    config = get_config()
    target_has_secret = target_url_has_webhook_secret(config.telegram_relay_target_url)
    return {
        "telegramRelayConfigured": bool(config.telegram_relay_target_url),
        "telegramRelaySecretConfigured": bool(config.webhook_shared_secret),
        "telegramRelayTargetHasSecretParam": target_has_secret,
        "telegramRelayWillAttachSecret": bool(config.webhook_shared_secret) or target_has_secret,
    }


def target_url_has_webhook_secret(target_url: str) -> bool:
    if not target_url:
        return False
    parsed = urlsplit(target_url)
    return query_has_webhook_secret(parse_qs(parsed.query, keep_blank_values=True))


def query_has_webhook_secret(query: dict[str, list[str]]) -> bool:
    for key in SECRET_QUERY_KEYS:
        if key in query and any(str(value).strip() for value in query[key]):
            return True
    return False


def relay_telegram_webhook(body: bytes, content_type: str) -> dict[str, Any]:
    config = get_config()
    target_url = build_relay_target_url()
    if not target_url:
        return {"ok": False, "error": "telegram_relay_target_missing", "statusCode": 500}

    try:
        upstream = requests.post(
            target_url,
            data=body,
            headers={"Content-Type": content_type or "application/json"},
            timeout=config.telegram_relay_timeout_seconds,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": f"telegram_relay_exception:{type(exc).__name__}",
            "statusCode": 502,
        }

    status_code = int(getattr(upstream, "status_code", 0) or 0)
    upstream_body = str(getattr(upstream, "text", "") or "")
    if 200 <= status_code < 300:
        if "invalid_webhook_secret" in upstream_body:
            return {"ok": False, "error": "telegram_relay_invalid_webhook_secret", "statusCode": 502}
        return {"ok": True, "statusCode": status_code}

    return {
        "ok": False,
        "error": "telegram_relay_http_error",
        "statusCode": status_code or 502,
        "upstreamBody": upstream_body[:500],
    }
