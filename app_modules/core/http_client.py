from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import requests

from app_modules.core.config import get_config


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status_code: int
    text: str
    final_url: str
    reason: str


def get_text(url: str, headers: Mapping[str, str] | None = None) -> HttpResult:
    config = get_config()
    try:
        response = requests.get(
            url,
            headers=dict(headers or {}),
            timeout=config.request_timeout_seconds,
            allow_redirects=True,
        )
        return HttpResult(
            ok=200 <= response.status_code < 400,
            status_code=response.status_code,
            text=response.text,
            final_url=response.url,
            reason="ok",
        )
    except requests.RequestException as exc:
        return HttpResult(
            ok=False,
            status_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{exc}",
        )
