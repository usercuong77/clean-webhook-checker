from __future__ import annotations

import os
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

import requests


DEFAULT_TDS_UID_ENDPOINT = "https://id.traodoisub.com/api.php"
DEFAULT_TDS_UID_TIMEOUT_SEC = 6.0
DEFAULT_TDS_UID_DEADLINE_SEC = 60.0
DEFAULT_TDS_UID_RATE_LIMIT_SLEEP_SEC = 7.0
DEFAULT_TDS_UID_TRANSPORT_SLEEP_SEC = 1.0


@dataclass(frozen=True)
class TdsUidResolution:
    uid: str
    name: str
    source: str
    reason: str
    http_code: int = 0
    elapsed_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.uid)


def resolve_uid_with_tds_api(
    raw: Any,
    timeout: float | None = None,
    deadline: float | None = None,
) -> TdsUidResolution:
    link = str(raw or "").strip()
    if not link:
        return TdsUidResolution("", "", "tds_uid_api", "empty_input")

    endpoint = os.getenv("TDS_UID_ENDPOINT", DEFAULT_TDS_UID_ENDPOINT).strip() or DEFAULT_TDS_UID_ENDPOINT
    request_timeout = _timeout_value(timeout)
    deadline_seconds = _deadline_value(deadline)
    headers = _tds_headers()
    started = time.monotonic()
    last_result = TdsUidResolution("", "", "tds_uid_api", "tds_api_no_attempt")

    while True:
        last_result = _tds_api_once(endpoint, link, headers, request_timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        last_result = _with_elapsed(last_result, elapsed_ms)

        if last_result.uid:
            return last_result

        remaining = deadline_seconds - (time.monotonic() - started)
        if last_result.reason == "tds_rate_limited":
            sleep_seconds = _rate_limit_sleep_value()
            if remaining > sleep_seconds:
                time.sleep(sleep_seconds)
                continue
            return last_result

        if _is_transport_failure(last_result.reason):
            sleep_seconds = _transport_sleep_value()
            if remaining > max(sleep_seconds, 0.5):
                time.sleep(min(sleep_seconds, remaining))
                continue
            return TdsUidResolution(
                "",
                last_result.name,
                "tds_uid_api",
                "tds_api_unavailable_after_deadline",
                http_code=last_result.http_code,
                elapsed_ms=elapsed_ms,
                raw=last_result.raw,
            )

        return last_result


def _tds_api_once(
    endpoint: str,
    link: str,
    headers: Mapping[str, str],
    request_timeout: float,
) -> TdsUidResolution:
    try:
        response = requests.post(
            endpoint,
            data={"link": link},
            headers=headers,
            timeout=request_timeout,
        )
    except requests.RequestException as exc:
        return TdsUidResolution("", "", "tds_uid_api", f"request_error:{type(exc).__name__}")

    try:
        payload = response.json()
    except ValueError:
        return TdsUidResolution(
            "",
            "",
            "tds_uid_api",
            "non_json_response",
            http_code=response.status_code,
            raw={"text": (response.text or "")[:300]},
        )

    uid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    success = str(payload.get("success") or payload.get("code") or "").strip().lower()
    error = str(payload.get("error") or payload.get("message") or "").strip()
    if response.status_code == 200 and uid and not error and (not success or success in {"200", "true", "ok", "success"}):
        return TdsUidResolution(
            uid,
            name,
            "tds_uid_api",
            "uid_found_tds_api",
            http_code=response.status_code,
            raw=payload,
        )

    reason = _tds_failure_reason(payload, response.status_code)
    return TdsUidResolution(
        "",
        name,
        "tds_uid_api",
        reason,
        http_code=response.status_code,
        raw=payload,
    )


def _tds_headers() -> Mapping[str, str]:
    return {
        "User-Agent": os.getenv("TDS_UID_USER_AGENT", "Mozilla/5.0").strip() or "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://id.traodoisub.com",
        "Referer": "https://id.traodoisub.com/",
    }


def _timeout_value(timeout: float | None) -> float:
    if timeout is not None:
        return max(0.5, float(timeout))
    raw = os.getenv("TDS_UID_TIMEOUT_SEC", "").strip()
    if not raw:
        return DEFAULT_TDS_UID_TIMEOUT_SEC
    try:
        return max(0.5, float(raw))
    except ValueError:
        return DEFAULT_TDS_UID_TIMEOUT_SEC


def _deadline_value(deadline: float | None) -> float:
    if deadline is not None:
        return max(0.5, float(deadline))
    raw = os.getenv("TDS_UID_DEADLINE_SEC", "").strip()
    if not raw:
        return DEFAULT_TDS_UID_DEADLINE_SEC
    try:
        return max(0.5, float(raw))
    except ValueError:
        return DEFAULT_TDS_UID_DEADLINE_SEC


def _rate_limit_sleep_value() -> float:
    raw = os.getenv("TDS_UID_RATE_LIMIT_SLEEP_SEC", "").strip()
    if not raw:
        return DEFAULT_TDS_UID_RATE_LIMIT_SLEEP_SEC
    try:
        return max(0.5, float(raw))
    except ValueError:
        return DEFAULT_TDS_UID_RATE_LIMIT_SLEEP_SEC


def _transport_sleep_value() -> float:
    raw = os.getenv("TDS_UID_TRANSPORT_SLEEP_SEC", "").strip()
    if not raw:
        return DEFAULT_TDS_UID_TRANSPORT_SLEEP_SEC
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_TDS_UID_TRANSPORT_SLEEP_SEC


def _tds_failure_reason(payload: Mapping[str, Any], http_code: int) -> str:
    error = str(payload.get("error") or payload.get("message") or "").strip()
    if error:
        lowered = _fold_text(error)
        if "cham" in lowered or "slow" in lowered:
            return "tds_rate_limited"
        if "khong ton tai" in lowered or "chua de che do cong khai" in lowered or "khong cong khai" in lowered:
            return "tds_link_not_found"
        return "tds_api_error"
    if http_code:
        return f"tds_http_{http_code}"
    return "tds_api_no_success"


def _fold_text(value: str) -> str:
    lowered = str(value or "").lower().replace("đ", "d").replace("Đ", "d")
    return unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode("ascii")


def _is_transport_failure(reason: str) -> bool:
    value = str(reason or "")
    return (
        value.startswith("request_error:")
        or value == "non_json_response"
        or value.startswith("tds_http_5")
    )


def _with_elapsed(result: TdsUidResolution, elapsed_ms: int) -> TdsUidResolution:
    return TdsUidResolution(
        result.uid,
        result.name,
        result.source,
        result.reason,
        http_code=result.http_code,
        elapsed_ms=elapsed_ms,
        raw=result.raw,
    )
