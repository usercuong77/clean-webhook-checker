import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from app_modules.core.render_registration import detect_public_base_url


_START_LOCK = threading.Lock()
_STARTED = False
_PRIMARY_HOST = "clean-webhook-checker.onrender.com"


def schedule_gateway_cron() -> bool:
    """Start the 60-second fallback scheduler on the primary Render only."""
    global _STARTED
    if not _scheduler_enabled():
        return False
    with _START_LOCK:
        if _STARTED:
            return False
        _STARTED = True
        thread = threading.Thread(target=_scheduler_loop, name="gateway-cron-fallback", daemon=True)
        thread.start()
    return True


def trigger_gateway_once() -> dict[str, Any]:
    endpoint = _scheduler_endpoint()
    secret = _first_env("RENDER_REGISTRATION_SECRET", "CLOUDFLARE_RENDER_REGISTRATION_SECRET")
    if not endpoint:
        return {"ok": False, "reason": "missing_scheduler_endpoint"}
    if not secret:
        return {"ok": False, "reason": "missing_registration_secret"}
    try:
        response = requests.post(
            endpoint,
            headers={
                "x-render-registration-secret": secret,
                "user-agent": "clean-webhook-checker/render-scheduler-1.0",
            },
            timeout=(3, 10),
        )
        return {
            "ok": 200 <= response.status_code < 300,
            "reason": "accepted" if 200 <= response.status_code < 300 else f"http_{response.status_code}",
            "httpCode": response.status_code,
        }
    except requests.RequestException as exc:
        return {"ok": False, "reason": f"request_error:{type(exc).__name__}"}


def _scheduler_loop() -> None:
    time.sleep(_startup_delay_seconds())
    while True:
        started = time.monotonic()
        trigger_gateway_once()
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, _interval_seconds() - elapsed))


def _scheduler_enabled() -> bool:
    explicit = os.getenv("RENDER_GATEWAY_SCHEDULER_ENABLED", "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    try:
        host = (urlparse(detect_public_base_url()).hostname or "").lower()
    except ValueError:
        host = ""
    return host == _PRIMARY_HOST


def _scheduler_endpoint() -> str:
    explicit = os.getenv("CLOUDFLARE_SCHEDULE_RUN_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    register_url = _first_env("CLOUDFLARE_RENDER_REGISTER_URL", "RENDER_REGISTRATION_URL")
    try:
        parsed = urlparse(register_url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/internal/scheduled/run"


def _interval_seconds() -> float:
    return _bounded_float("RENDER_GATEWAY_SCHEDULER_INTERVAL_SEC", 60.0, 30.0, 300.0)


def _startup_delay_seconds() -> float:
    return _bounded_float("RENDER_GATEWAY_SCHEDULER_STARTUP_DELAY_SEC", 8.0, 0.0, 60.0)


def _bounded_float(key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(key, "") or default)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""
