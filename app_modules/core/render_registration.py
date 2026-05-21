import os
import threading
from typing import Any

import requests

from app_modules.core.config import get_config


def schedule_render_registration() -> None:
    """Register this Render service with the Cloudflare gateway in the background."""
    thread = threading.Thread(target=register_render_service_once, name="render-self-register", daemon=True)
    thread.start()


def register_render_service_once() -> dict[str, Any]:
    endpoint = _first_env("CLOUDFLARE_RENDER_REGISTER_URL", "RENDER_REGISTRATION_URL")
    secret = _first_env("RENDER_REGISTRATION_SECRET", "CLOUDFLARE_RENDER_REGISTRATION_SECRET")
    base_url = detect_public_base_url()

    if not endpoint:
        return {"ok": False, "reason": "missing_register_url"}
    if not secret:
        return {"ok": False, "reason": "missing_registration_secret"}
    if not base_url:
        return {"ok": False, "reason": "missing_public_base_url"}

    payload = {
        "baseUrl": base_url,
        "serviceName": _first_env("RENDER_SERVICE_NAME", "SERVICE_NAME", "APP_NAME"),
        "serviceId": _first_env("RENDER_SERVICE_ID", "SERVICE_ID"),
        "version": get_config().version,
    }

    try:
        timeout = _registration_timeout_seconds()
        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "x-render-registration-secret": secret,
                "user-agent": "clean-webhook-checker-render-self-register/1.0",
            },
            timeout=timeout,
        )
        body = _safe_json(response)
        return {
            "ok": response.status_code >= 200 and response.status_code < 300 and body.get("ok") is not False,
            "reason": body.get("reason") or f"http_{response.status_code}",
            "httpCode": response.status_code,
            "baseUrl": base_url,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "reason": f"registration_request_error:{type(exc).__name__}",
            "baseUrl": base_url,
        }


def detect_public_base_url() -> str:
    explicit = _first_env(
        "RENDER_PUBLIC_URL",
        "PUBLIC_RENDER_URL",
        "RENDER_EXTERNAL_URL",
        "SERVICE_URL",
        "PUBLIC_BASE_URL",
    )
    if explicit:
        return _normalize_base_url(explicit)

    service_name = _first_env("RENDER_SERVICE_NAME", "SERVICE_NAME")
    if service_name:
        return _normalize_base_url(f"https://{service_name}.onrender.com")

    return ""


def _registration_timeout_seconds() -> float:
    raw = os.getenv("RENDER_REGISTRATION_TIMEOUT_SEC", "").strip()
    try:
        parsed = float(raw)
    except ValueError:
        parsed = 5.0
    return max(1.0, min(parsed, 10.0))


def _normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
