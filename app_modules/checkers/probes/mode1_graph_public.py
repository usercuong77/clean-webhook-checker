from __future__ import annotations

from typing import Any

import requests

from app_modules.core.config import get_config
from app_modules.checkers.probe_result import ProbeResult


def probe_mode1_graph_public(uid: str) -> ProbeResult:
    normalized_uid = str(uid or "").strip()
    if not normalized_uid.isdigit():
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode1_graph_public",
            reason="numeric_uid_required",
            http_code=0,
            details={},
        )

    config = get_config()
    url = f"https://graph.facebook.com/{normalized_uid}/picture?type=normal&redirect=false"
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
            status="DIE",
            confidence="weak",
            source="mode1_graph_public",
            reason=f"request_error:{exc}",
            http_code=0,
            details={"url": url},
        )

    http_code = int(response.status_code or 0)
    if http_code in {404, 410}:
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode1_graph_public",
            reason=f"graph_http_{http_code}",
            http_code=http_code,
            details={"url": url},
        )

    payload = _parse_json_response(response)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "").lower()
        if "unsupported get request" in message or "cannot be loaded due to missing permissions" in message:
            return ProbeResult(
                status="DIE",
                confidence="strong",
                source="mode1_graph_public",
                reason="graph_error_unsupported",
                http_code=http_code,
                details={"message": message},
            )
        return ProbeResult(
            status="DIE",
            confidence="strong",
            source="mode1_graph_public",
            reason="graph_error",
            http_code=http_code,
            details={"message": message},
        )

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    image_url = str(data.get("url") or "")
    is_silhouette = data.get("is_silhouette")
    has_dimensions = _positive_number(data.get("height")) and _positive_number(data.get("width"))

    if has_dimensions:
        return ProbeResult(
            status="LIVE",
            confidence="strong",
            source="mode1_graph_public",
            reason="graph_profile_picture_dimensions",
            http_code=http_code,
            details={
                "imageUrl": image_url,
                "height": data.get("height"),
                "width": data.get("width"),
                "isSilhouette": is_silhouette,
            },
        )

    return ProbeResult(
        status="DIE",
        confidence="strong",
        source="mode1_graph_public",
        reason="graph_missing_picture_dimensions",
        http_code=http_code,
        details={
            "imageUrl": image_url,
            "height": data.get("height"),
            "width": data.get("width"),
            "isSilhouette": is_silhouette,
        },
    )


def _parse_json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False
