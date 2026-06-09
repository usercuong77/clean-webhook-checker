from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app_modules.resolvers import fb_uid_lite_latest as lite


@dataclass(frozen=True)
class LiteUidResolution:
    input: str
    uid: str
    source: str
    reason: str
    score: int = 0
    elapsed_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.uid)


async def resolve_uid_with_lite(raw: Any) -> LiteUidResolution:
    results = await resolve_uid_with_lite_many([raw])
    return results[0] if results else LiteUidResolution(str(raw or ""), "", "fb_uid_lite", "empty_result")


def resolve_uid_with_lite_sync(raw: Any) -> LiteUidResolution:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(resolve_uid_with_lite(raw))
        except Exception as exc:
            return LiteUidResolution(
                input=str(raw or ""),
                uid="",
                source="fb_uid_lite",
                reason=f"lite_exception:{type(exc).__name__}",
                raw={"error": str(exc)[:300]},
            )

    return LiteUidResolution(
        input=str(raw or ""),
        uid="",
        source="fb_uid_lite",
        reason="lite_event_loop_running",
    )


async def resolve_uid_with_lite_many(raw_inputs: list[Any]) -> list[LiteUidResolution]:
    values = [str(item or "").strip() for item in raw_inputs]
    if not values:
        return []

    async with lite.make_client(False) as client, lite.make_client(True) as mobile_client:
        results = await asyncio.gather(
            *[lite.resolve(client, mobile_client, value) for value in values],
            return_exceptions=True,
        )

    out: list[LiteUidResolution] = []
    for value, item in zip(values, results):
        if isinstance(item, Exception):
            out.append(
                LiteUidResolution(
                    input=value,
                    uid="",
                    source="fb_uid_lite",
                    reason=f"lite_exception:{type(item).__name__}",
                    raw={"error": str(item)[:300]},
                )
            )
            continue
        payload = item if isinstance(item, dict) else {}
        out.append(_from_lite_payload(value, payload))
    return out


def _from_lite_payload(value: str, payload: dict[str, Any]) -> LiteUidResolution:
    uid = str(payload.get("uid") or "").strip()
    strategy = str(payload.get("strategy") or "fb_uid_lite").strip() or "fb_uid_lite"
    reason = "uid_found_lite" if uid else str(payload.get("reason") or "lite_no_uid").strip()
    score = _to_int(payload.get("score"), 0)
    elapsed_ms = _to_int(payload.get("elapsed_ms"), 0)
    return LiteUidResolution(
        input=value,
        uid=uid,
        source=f"fb_uid_lite:{strategy}",
        reason=reason,
        score=score,
        elapsed_ms=elapsed_ms,
        raw=payload,
    )


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
