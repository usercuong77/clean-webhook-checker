from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import requests


DEFAULT_TDS_UID_ENDPOINT = "https://id.traodoisub.com/api.php"
DEFAULT_TDS_UID_TIMEOUT_SEC = 6.0


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


def resolve_uid_with_tds_api(raw: Any, timeout: float | None = None) -> TdsUidResolution:
    link = str(raw or "").strip()
    if not link:
        return TdsUidResolution("", "", "tds_uid_api", "empty_input")

    endpoint = os.getenv("TDS_UID_ENDPOINT", DEFAULT_TDS_UID_ENDPOINT).strip() or DEFAULT_TDS_UID_ENDPOINT
    request_timeout = _timeout_value(timeout)
    headers = _tds_headers()

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
    success = str(payload.get("success") or payload.get("code") or "").strip()
    if response.status_code == 200 and uid and success == "200":
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


def _tds_failure_reason(payload: Mapping[str, Any], http_code: int) -> str:
    error = str(payload.get("error") or payload.get("message") or "").strip()
    if error:
        lowered = error.lower()
        if "chậm" in lowered or "cham" in lowered or "slow" in lowered:
            return "tds_rate_limited"
        if "không tồn tại" in lowered or "khong ton tai" in lowered:
            return "tds_link_not_found"
        return "tds_api_error"
    if http_code:
        return f"tds_http_{http_code}"
    return "tds_api_no_success"
