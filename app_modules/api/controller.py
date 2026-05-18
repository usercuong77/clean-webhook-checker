from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from app_modules.checkers.live_die import check_live_die
from app_modules.core.config import get_config
from app_modules.features.latest_post import get_latest_post
from app_modules.features.profile_name import choose_profile_name
from app_modules.resolvers.uid_resolver import resolve_input


Status = Literal["LIVE", "DIE", "UNKNOWN"]
Confidence = Literal["strong", "weak"]


class CheckRequest(BaseModel):
    input: str = Field(default="")
    mode: str = Field(default="all")
    includeName: bool = Field(default=True)


class LatestPostRequest(BaseModel):
    input: str = Field(default="")
    uid: str = Field(default="")
    url: str = Field(default="")
    cookies: dict[str, Any] | None = Field(default=None)
    cookiesPool: list[dict[str, Any]] | None = Field(default=None)
    cookies_pool: list[dict[str, Any]] | None = Field(default=None)


class RealtimeBulkJob(BaseModel):
    id: str = Field(default="")
    type: str = Field(default="uid")
    input: str = Field(default="")
    uid: str = Field(default="")
    mode: str = Field(default="all")
    includeName: bool = Field(default=False)


class RealtimeBulkRequest(BaseModel):
    jobs: list[RealtimeBulkJob] = Field(default_factory=list)


def health_payload() -> dict[str, Any]:
    config = get_config()
    return {
        "ok": True,
        "service": config.app_name,
        "version": config.version,
    }


def check_input(req: CheckRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or "").strip()
    resolved = resolve_input(raw_input)
    live_die = check_live_die(resolved, mode=req.mode)
    name = choose_profile_name(resolved, live_die, include_name=req.includeName)
    elapsed_ms = int((perf_counter() - started) * 1000)

    return {
        "ok": True,
        "status": live_die.status,
        "confidence": live_die.confidence,
        "uid": resolved.uid,
        "username": resolved.username,
        "name": name,
        "canonicalUrl": resolved.canonical_url,
        "source": live_die.source,
        "reason": live_die.reason,
        "httpCode": live_die.http_code,
        "elapsedMs": elapsed_ms,
        "probes": live_die.probes,
        "resolverDebug": _resolver_debug_summary(resolved),
    }


def latest_post_input(req: LatestPostRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or req.uid or req.url or "").strip()
    resolved = resolve_input(raw_input)
    result = get_latest_post(
        resolved,
        request_cookies=req.cookies,
        request_cookie_pool=req.cookiesPool or req.cookies_pool,
    )
    result["elapsedMs"] = int((perf_counter() - started) * 1000)
    result["username"] = resolved.username
    result["canonicalUrl"] = resolved.canonical_url
    return result


def _resolver_debug_summary(resolved) -> dict[str, Any]:
    probes = list(getattr(resolved, "resolver_probes", []) or [])
    reasons: dict[str, int] = {}
    headers: list[str] = []
    sources: list[str] = []
    successful_probe: dict[str, Any] | None = None

    for probe in probes:
        if not isinstance(probe, dict):
            continue
        reason = str(probe.get("reason", "") or "")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        source = str(probe.get("source", "") or "")
        if source and source not in sources:
            sources.append(source)
        header = str(probe.get("header") or probe.get("userAgent") or "").strip()
        if header and header not in headers:
            headers.append(header[:100])
        if successful_probe is None and (probe.get("foundUid") or reason.startswith("uid_found_")):
            successful_probe = {
                "source": source,
                "reason": reason,
                "uid": str(probe.get("foundUid", "") or ""),
                "url": str(probe.get("url", "") or ""),
                "header": header[:100],
                "httpCode": int(probe.get("httpCode") or 0),
            }

    last_probe = probes[-1] if probes and isinstance(probes[-1], dict) else {}
    return {
        "source": getattr(resolved, "source", ""),
        "reason": getattr(resolved, "reason", ""),
        "uid": getattr(resolved, "uid", ""),
        "username": getattr(resolved, "username", ""),
        "canonicalUrl": getattr(resolved, "canonical_url", ""),
        "needsNetworkResolve": bool(getattr(resolved, "needs_network_resolve", False)),
        "probeCount": len(probes),
        "sources": sources[:8],
        "headers": headers[:8],
        "reasonCounts": reasons,
        "lastReason": str(last_probe.get("reason", "") or ""),
        "lastHttpCode": int(last_probe.get("httpCode") or 0) if last_probe else 0,
        "successfulProbe": successful_probe or {},
    }


def realtime_check_bulk(req: RealtimeBulkRequest) -> dict[str, Any]:
    started = perf_counter()
    results: list[dict[str, Any]] = []

    for index, job in enumerate(req.jobs or []):
        job_id = (job.id or f"job_{index + 1}").strip()
        job_type = (job.type or "uid").strip().lower()
        if job_type != "uid":
            results.append(
                {
                    "id": job_id,
                    "type": job_type,
                    "ok": False,
                    "reason": "unsupported_job_type",
                    "status": "UNKNOWN",
                    "uid": "",
                }
            )
            continue

        raw_input = (job.input or job.uid or "").strip()
        if not raw_input:
            results.append(
                {
                    "id": job_id,
                    "type": "uid",
                    "ok": False,
                    "reason": "empty_input",
                    "status": "UNKNOWN",
                    "uid": "",
                }
            )
            continue

        try:
            item = check_input(
                CheckRequest(
                    input=raw_input,
                    mode=job.mode or "all",
                    includeName=bool(job.includeName),
                )
            )
            item["id"] = job_id
            item["type"] = "uid"
            results.append(item)
        except Exception as exc:
            results.append(
                {
                    "id": job_id,
                    "type": "uid",
                    "ok": False,
                    "reason": f"job_error:{type(exc).__name__}",
                    "status": "UNKNOWN",
                    "uid": "",
                    "httpCode": 0,
                    "elapsedMs": 0,
                }
            )

    return {
        "ok": True,
        "results": results,
        "jobCount": len(req.jobs or []),
        "elapsedMs": int((perf_counter() - started) * 1000),
    }
