from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

import requests


@dataclass(frozen=True)
class ProbeDocument:
    http_code: int
    text: str
    final_url: str
    reason: str
    elapsed_ms: int
    bytes_read: int
    complete: bool


def fetch_profile_document(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    stop_markers: tuple[bytes, ...] = (),
    session: requests.Session | None = None,
) -> ProbeDocument:
    started = time.perf_counter()
    chunks: list[bytes] = []
    total = 0
    tail = b""
    complete = False
    try:
        client = session or requests
        with client.get(
            url,
            headers=dict(headers),
            timeout=_requests_timeout(timeout),
            allow_redirects=True,
            stream=True,
        ) as response:
            encoding = response.encoding or "utf-8"
            deadline = max(0.6, float(timeout))
            iterator = response.iter_content(chunk_size=16_384)
            for chunk in iterator:
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if stop_markers:
                    scan = (tail + chunk).lower()
                    if any(marker.lower() in scan for marker in stop_markers):
                        break
                    tail = scan[-2048:]
                if total >= max_bytes or time.perf_counter() - started >= deadline:
                    break
            else:
                complete = True
            text = b"".join(chunks).decode(encoding, errors="ignore")
            return ProbeDocument(
                http_code=response.status_code,
                text=text,
                final_url=response.url or url,
                reason="ok" if 200 <= response.status_code < 400 else f"http_{response.status_code}",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                bytes_read=total,
                complete=complete,
            )
    except requests.RequestException as exc:
        return ProbeDocument(
            http_code=0,
            text="",
            final_url=url,
            reason=f"request_error:{type(exc).__name__}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            bytes_read=total,
            complete=False,
        )


def _requests_timeout(timeout: float) -> tuple[float, float]:
    read_timeout = max(0.7, float(timeout))
    return (max(0.4, min(0.9, read_timeout / 2)), read_timeout)
