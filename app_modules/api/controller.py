from time import perf_counter
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from app_modules.checkers.live_die import check_live_die
from app_modules.core.config import get_config
from app_modules.features.latest_post import get_latest_post, get_latest_post_direct_from_input, sanitize_latest_post_input
from app_modules.features.profile_name import choose_profile_name, resolve_profile_name
from app_modules.features.viplike import create_viplike_order, get_viplike_packages
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
    url: str = Field(default="")
    mode: str = Field(default="all")
    includeName: bool = Field(default=False)


class RealtimeBulkRequest(BaseModel):
    jobs: list[RealtimeBulkJob] = Field(default_factory=list)


class VipLikeOrderRequest(BaseModel):
    uid: str = Field(default="")
    postId: str = Field(default="")
    post_id: str = Field(default="")
    objectId: str = Field(default="")
    object_id: str = Field(default="")
    postLink: str = Field(default="")
    post_link: str = Field(default="")
    link: str = Field(default="")
    packageName: str = Field(default="")
    package_name: str = Field(default="")
    endpoint: str = Field(default="")
    quantity: int = Field(default=0)
    reactionType: str = Field(default="")
    reaction_type: str = Field(default="")
    reactionTypes: list[str] | None = Field(default=None)
    reaction_types: list[str] | None = Field(default=None)
    reactions: list[str] | str | None = Field(default=None)
    dedupeKey: str = Field(default="")
    dedupe_key: str = Field(default="")
    confirm: bool = Field(default=False)
    dryRun: bool = Field(default=False)
    dry_run: bool = Field(default=False)


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
    name = choose_profile_name(
        resolved,
        live_die,
        include_name=bool(req.includeName) and _profile_name_lookup_enabled(),
    ) or str(getattr(resolved, "resolver_name", "") or "").strip()
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


def check_name_input(req: CheckRequest) -> dict[str, Any]:
    return check_tick_input(req)


def check_tick_input(req: CheckRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or "").strip()
    resolved = resolve_input(raw_input)
    live_die = check_live_die(resolved, mode=req.mode)

    if live_die.status == "LIVE":
        name_result = resolve_profile_name(resolved)
        name = name_result.name or resolved.username or resolved.uid
    else:
        name_result = None
        name = ""

    verified_label = _verified_account_label(name)
    elapsed_ms = int((perf_counter() - started) * 1000)
    return {
        "ok": True,
        "status": live_die.status,
        "confidence": live_die.confidence,
        "uid": resolved.uid,
        "username": resolved.username,
        "name": name,
        "displayName": _display_profile_name(name),
        "verified": bool(verified_label),
        "isVerified": bool(verified_label),
        "verifiedLabel": verified_label,
        "canonicalUrl": resolved.canonical_url,
        "source": name_result.source if name_result else live_die.source,
        "reason": name_result.reason if name_result else f"checktick_skipped:{live_die.reason}",
        "httpCode": live_die.http_code,
        "elapsedMs": elapsed_ms,
        "probes": live_die.probes,
        "nameSource": name_result.source if name_result else "",
        "nameReason": name_result.reason if name_result else "account_not_live",
        "nameProbes": name_result.probes if name_result else [],
        "resolverDebug": _resolver_debug_summary(resolved),
    }


def _profile_name_lookup_enabled() -> bool:
    value = os.getenv("PROFILE_NAME_LOOKUP_ENABLED", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    result["name"] = str(getattr(resolved, "resolver_name", "") or "").strip()
    result["canonicalUrl"] = resolved.canonical_url
    return result


def checkpost_direct_input(req: LatestPostRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or req.url or req.uid or "").strip()
    cleaned_input = sanitize_latest_post_input(raw_input)
    result = get_latest_post_direct_from_input(
        cleaned_input,
        request_cookies=req.cookies,
        request_cookie_pool=req.cookiesPool or req.cookies_pool,
    )
    if result.get("taggedPostSkipped") or result.get("needsOwnerResolve"):
        resolved = resolve_input(cleaned_input)
        owner_uid = str(getattr(resolved, "uid", "") or result.get("ownerUid") or "").strip()
        owner_name = str(getattr(resolved, "resolver_name", "") or "").strip()
        if owner_uid:
            retry = get_latest_post_direct_from_input(
                cleaned_input,
                request_cookies=req.cookies,
                request_cookie_pool=req.cookiesPool or req.cookies_pool,
                owner_uid=owner_uid,
                owner_name=owner_name,
                prefer_cookie=True,
            )
            retry["uid"] = owner_uid
            retry["name"] = owner_name
            retry["username"] = str(getattr(resolved, "username", "") or retry.get("username") or result.get("username") or "")
            retry["canonicalUrl"] = str(getattr(resolved, "canonical_url", "") or cleaned_input)
            retry["ownerResolveSource"] = str(getattr(resolved, "source", "") or "")
            retry["ownerResolveReason"] = str(getattr(resolved, "reason", "") or "")
            retry["ownerResolvedByTds"] = str(getattr(resolved, "source", "") or "") == "tds_uid_api"
            retry["skippedTaggedPost"] = True
            retry["elapsedMs"] = int((perf_counter() - started) * 1000)
            retry["input"] = cleaned_input
            return retry
        result["ownerResolveSource"] = str(getattr(resolved, "source", "") or "")
        result["ownerResolveReason"] = str(getattr(resolved, "reason", "") or "")
    result["elapsedMs"] = int((perf_counter() - started) * 1000)
    result["input"] = cleaned_input
    result["canonicalUrl"] = cleaned_input
    return result


def _verified_account_label(name: str) -> str:
    value = str(name or "").strip()
    low = value.lower()
    if "tài khoản đã xác minh" in low:
        return "Tài khoản đã xác minh"
    if "tài khoản đã xác minh" in low:
        return "Tài khoản đã xác minh"
    if "verified account" in low:
        return "Verified account"
    return ""


def _display_profile_name(name: str) -> str:
    value = str(name or "").strip()
    value = value.replace("Tài khoản đã xác minh", "").replace("Verified account", "").strip()
    for marker in ("Tài khoản đã xác minh", "Verified account"):
        value = value.replace(marker, "").strip()
    return value


def viplike_packages_input(refresh: bool = False, include_raw: bool = False) -> dict[str, Any]:
    return get_viplike_packages(refresh=refresh, include_raw=include_raw)


def viplike_order_input(req: VipLikeOrderRequest) -> dict[str, Any]:
    return create_viplike_order(req.dict())


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
        "name": getattr(resolved, "resolver_name", ""),
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
        if job_type not in {"uid", "post"}:
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

        raw_input = (job.input or job.uid or job.url or "").strip()
        if not raw_input:
            results.append(
                {
                    "id": job_id,
                    "type": job_type,
                    "ok": False,
                    "reason": "empty_input",
                    "status": "UNKNOWN",
                    "uid": "",
                }
            )
            continue

        try:
            if job_type == "post":
                item = latest_post_input(
                    LatestPostRequest(
                        input=raw_input,
                        uid=job.uid,
                        url=job.url,
                    )
                )
                item["id"] = job_id
                item["type"] = "post"
            else:
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
                    "type": job_type,
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
