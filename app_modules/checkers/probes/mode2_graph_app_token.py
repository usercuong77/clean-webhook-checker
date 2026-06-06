from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from app_modules.core.config import get_config
from app_modules.checkers.probe_result import ProbeResult


def probe_mode2_graph_app_token(uid: str) -> ProbeResult:
    normalized_uid = str(uid or "").strip()
    if not normalized_uid.isdigit():
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode2_graph_app_token",
            reason="numeric_uid_required",
            http_code=0,
            details={"uid": normalized_uid, "mode": "2"},
        )

    config = get_config()
    token = str(config.facebook_graph_picture_token or "").strip()
    if not token:
        return ProbeResult(
            status="UNKNOWN",
            confidence="weak",
            source="mode2_graph_app_token",
            reason="missing_graph_picture_token",
            http_code=0,
            details={"uid": normalized_uid, "mode": "2"},
        )

    url = (
        f"https://graph.facebook.com/{normalized_uid}/picture"
        f"?width=1080&height=1080&redirect=false&access_token={token}"
    )
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CleanRebuildBot/1.0)",
                "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=config.request_timeout_seconds,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return ProbeResult(
            status="UNKNOWN",
            confidence="weak",
            source="mode2_graph_app_token",
            reason=f"request_error:{exc}",
            http_code=0,
            details={"url": _mask_token(url, token), "uid": normalized_uid, "mode": "2"},
        )

    http_code = int(response.status_code or 0)
    payload = _parse_json_response(response)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode2_graph_app_token",
            reason=_graph_error_reason(error),
            http_code=http_code,
            details={
                "message": str(error.get("message") or "")[:220],
                "code": error.get("code"),
                "type": error.get("type"),
                "uid": normalized_uid,
                "mode": "2",
            },
        )

    if not 200 <= http_code < 400:
        return ProbeResult(
            status="UNKNOWN",
            confidence="weak",
            source="mode2_graph_app_token",
            reason=f"graph_http_{http_code}",
            http_code=http_code,
            details={"uid": normalized_uid, "mode": "2"},
        )

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    image_url = str(data.get("url") or "")
    is_silhouette = data.get("is_silhouette")
    has_dimensions = _positive_number(data.get("height")) and _positive_number(data.get("width"))
    is_default = _looks_like_default_avatar(image_url, is_silhouette)

    if image_url and has_dimensions and not is_default:
        return ProbeResult(
            status="LIVE",
            confidence="strong",
            source="mode2_graph_app_token",
            reason="graph_token_profile_picture_real_image",
            http_code=http_code,
            details={
                "imageUrl": _mask_token(image_url, token),
                "height": data.get("height"),
                "width": data.get("width"),
                "isSilhouette": is_silhouette,
                "uid": normalized_uid,
                "mode": "2",
            },
        )

    if image_url and is_default:
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode2_graph_app_token",
            reason="graph_token_default_or_silhouette_avatar",
            http_code=http_code,
            details={
                "imageUrl": _mask_token(image_url, token),
                "height": data.get("height"),
                "width": data.get("width"),
                "isSilhouette": is_silhouette,
                "uid": normalized_uid,
                "mode": "2",
            },
        )

    return ProbeResult(
        status="UNKNOWN",
        confidence="weak",
        source="mode2_graph_app_token",
        reason="graph_token_missing_stable_picture_signal",
        http_code=http_code,
        details={
            "imageUrl": _mask_token(image_url, token),
            "height": data.get("height"),
            "width": data.get("width"),
            "isSilhouette": is_silhouette,
            "uid": normalized_uid,
            "mode": "2",
        },
    )


def _parse_json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _graph_error_reason(error: dict[str, Any]) -> str:
    message = str(error.get("message") or "").lower()
    if "unsupported get request" in message or "does not exist" in message:
        return "graph_token_error_unsupported"
    if "missing permissions" in message or "cannot be loaded" in message:
        return "graph_token_error_unavailable"
    return "graph_token_error"


def _looks_like_default_avatar(image_url: str, is_silhouette: Any) -> bool:
    lowered = str(image_url or "").lower()
    return (
        bool(is_silhouette)
        or lowered.endswith(".gif")
        or "static.xx.fbcdn.net" in lowered
        or "/rsrc.php/" in lowered
    )


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _mask_token(value: str, token: str) -> str:
    if token:
        value = value.replace(token, "***")
        value = value.replace(quote(token, safe=""), "***")
    return value
