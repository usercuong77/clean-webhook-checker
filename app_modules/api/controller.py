from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from time import perf_counter
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from app_modules.checkers.live_die import check_live_die
from app_modules.core.config import get_config
from app_modules.features.cookie_status import get_cookie_status
from app_modules.features.latest_post import get_latest_post, get_latest_post_direct_from_input, sanitize_latest_post_input
from app_modules.features.profile_tick import resolve_profile_tick_from_input
from app_modules.features.profile_name_resolver import choose_profile_name
from app_modules.features.viplike import create_viplike_order, get_viplike_packages
from app_modules.resolvers.facebook_cookies import reload_cookie_accounts_cache
from app_modules.resolvers import facebook_uid_resolver
from app_modules.resolvers.fb_uid_lite_adapter import resolve_uid_with_lite_many
from app_modules.resolvers.tds_uid_resolver import TdsUidResolution, resolve_uid_with_tds_api
from app_modules.resolvers.uid_resolver import ResolvedInput
from app_modules.resolvers.uid_resolver import resolve_input


Status = Literal["LIVE", "DIE", "UNKNOWN"]
Confidence = Literal["strong", "weak"]


class CheckRequest(BaseModel):
    input: str = Field(default="")
    mode: str = Field(default="all")
    includeName: bool = Field(default=True)
    forceCookie: bool = Field(default=False)


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


class UidResolverCompareRequest(BaseModel):
    input: str = Field(default="")
    inputs: list[str] = Field(default_factory=list)
    includeRaw: bool = Field(default=False)


class UidResolveFastRequest(BaseModel):
    input: str = Field(default="")


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
        "codeVersion": "step160-checktick-accuracy-fast-negative",
    }


def cookie_status_input() -> dict[str, Any]:
    return get_cookie_status()


def cookie_reload_input() -> dict[str, Any]:
    reload_cookie_accounts_cache()
    return {
        "ok": True,
        "reason": "cookie_cache_reloaded",
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
    name = _enrich_direct_profile_url_name(raw_input, resolved, live_die.status, name, bool(req.includeName))
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


async def uid_resolver_compare_input(req: UidResolverCompareRequest) -> dict[str, Any]:
    started = perf_counter()
    inputs = _compare_inputs(req)
    lite_results = await resolve_uid_with_lite_many(inputs)
    rows: list[dict[str, Any]] = []

    for raw_input, lite in zip(inputs, lite_results):
        internal_started = perf_counter()
        with _tds_disabled_for_internal_compare():
            internal = facebook_uid_resolver.resolve_uid_from_any_input(raw_input)
        internal_elapsed = int((perf_counter() - internal_started) * 1000)

        tds_started = perf_counter()
        tds = resolve_uid_with_tds_api(
            raw_input,
            timeout=_compare_tds_timeout(),
            deadline=_compare_tds_deadline(),
        )
        tds_elapsed = int((perf_counter() - tds_started) * 1000)

        row = {
            "input": raw_input,
            "lite": {
                "ok": bool(lite.uid),
                "uid": lite.uid,
                "source": lite.source,
                "reason": lite.reason,
                "score": lite.score,
                "elapsedMs": lite.elapsed_ms,
            },
            "internalNoTds": {
                "ok": bool(internal.uid),
                "uid": internal.uid,
                "username": internal.username,
                "source": internal.source,
                "reason": internal.reason,
                "elapsedMs": internal_elapsed,
                "probeCount": len(
                    getattr(internal, "probes", None)
                    or getattr(internal, "resolver_probes", None)
                    or []
                ),
            },
            "tds": {
                "ok": bool(tds.uid),
                "uid": tds.uid,
                "name": tds.name,
                "source": tds.source,
                "reason": tds.reason,
                "httpCode": tds.http_code,
                "elapsedMs": tds_elapsed,
            },
        }
        if req.includeRaw:
            row["lite"]["raw"] = lite.raw
            row["tds"]["raw"] = tds.raw
        rows.append(row)

    return {
        "ok": True,
        "source": "uid_resolver_compare",
        "elapsedMs": int((perf_counter() - started) * 1000),
        "count": len(rows),
        "results": rows,
    }


async def uid_resolve_fast_input(req: UidResolveFastRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or "").strip()
    normalized = facebook_uid_resolver.normalize_url_input(raw_input) or raw_input
    direct_uid = (
        facebook_uid_resolver.normalize_uid(raw_input)
        or facebook_uid_resolver.extract_uid_from_url(normalized)
    )
    username = facebook_uid_resolver.extract_username_from_url(normalized)

    if direct_uid:
        return _uid_resolve_fast_success(
            raw_input=raw_input,
            uid=direct_uid,
            username=username,
            name="",
            canonical_url=f"https://www.facebook.com/profile.php?id={direct_uid}",
            source="uid_resolve_fast:direct",
            reason="uid_found_direct",
            http_code=200,
            started=started,
        )

    lite = (await resolve_uid_with_lite_many([normalized or raw_input]))[0]
    if lite.uid:
        return _uid_resolve_fast_success(
            raw_input=raw_input,
            uid=lite.uid,
            username=username,
            name=str(lite.raw.get("name") or "").strip(),
            canonical_url=f"https://www.facebook.com/profile.php?id={lite.uid}",
            source=lite.source or "fb_uid_lite",
            reason=lite.reason or "uid_found_lite",
            http_code=200,
            started=started,
        )

    tds_started = perf_counter()
    tds = resolve_uid_with_tds_api(
        normalized or raw_input,
        timeout=_uid_resolve_fast_tds_timeout(),
        deadline=_uid_resolve_fast_tds_deadline(),
    )
    tds_elapsed = int((perf_counter() - tds_started) * 1000)
    if tds.uid:
        return _uid_resolve_fast_success(
            raw_input=raw_input,
            uid=tds.uid,
            username=username,
            name=tds.name,
            canonical_url=f"https://www.facebook.com/profile.php?id={tds.uid}",
            source=tds.source or "tds_uid_api",
            reason=tds.reason or "uid_found_tds_api",
            http_code=tds.http_code or 200,
            started=started,
        )

    return {
        "ok": False,
        "status": "DIE",
        "confidence": "weak",
        "uid": "",
        "username": username,
        "name": "",
        "canonicalUrl": normalized,
        "source": "uid_resolve_fast",
        "reason": f"uid_not_found_lite_tds:lite={lite.reason}:tds={tds.reason}",
        "httpCode": tds.http_code or 200,
        "elapsedMs": int((perf_counter() - started) * 1000),
        "tdsElapsedMs": tds_elapsed,
    }


def _uid_resolve_fast_success(
    *,
    raw_input: str,
    uid: str,
    username: str,
    name: str,
    canonical_url: str,
    source: str,
    reason: str,
    http_code: int,
    started: float,
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "LIVE",
        "confidence": "strong",
        "input": raw_input,
        "uid": str(uid or "").strip(),
        "username": str(username or "").strip(),
        "name": str(name or "").strip(),
        "canonicalUrl": canonical_url,
        "source": source,
        "reason": reason,
        "httpCode": http_code,
        "elapsedMs": int((perf_counter() - started) * 1000),
    }


def _uid_resolve_fast_tds_timeout() -> float:
    try:
        return max(0.5, min(float(os.getenv("UID_RESOLVE_FAST_TDS_TIMEOUT_SEC", "3")), 6.0))
    except ValueError:
        return 3.0


def _uid_resolve_fast_tds_deadline() -> float:
    try:
        return max(0.5, min(float(os.getenv("UID_RESOLVE_FAST_TDS_DEADLINE_SEC", "4")), 8.0))
    except ValueError:
        return 4.0


def check_name_input(req: CheckRequest) -> dict[str, Any]:
    return check_tick_input(req)


def check_tick_input(req: CheckRequest) -> dict[str, Any]:
    started = perf_counter()
    raw_input = (req.input or "").strip()
    tick = resolve_profile_tick_from_input(raw_input, force_cookie=bool(req.forceCookie))
    name = tick.name
    verified_label = tick.verified_label or _verified_account_label(name)
    status: Status = _profile_tick_status(name, verified_label, tick.reason, tick.http_code)
    status = _profile_tick_status_with_uid(raw_input, tick, status)
    elapsed_ms = int((perf_counter() - started) * 1000)
    return {
        "ok": True,
        "status": status,
        "confidence": "strong" if status == "LIVE" else "weak",
        "uid": tick.uid,
        "username": tick.username,
        "name": name,
        "displayName": _display_profile_name(name),
        "verified": bool(verified_label),
        "isVerified": bool(verified_label),
        "verifiedLabel": verified_label,
        "canonicalUrl": tick.canonical_url,
        "source": tick.source,
        "reason": tick.reason,
        "httpCode": tick.http_code,
        "elapsedMs": elapsed_ms,
        "probes": tick.probes,
        "nameSource": tick.source,
        "nameReason": tick.reason,
        "nameProbes": tick.probes,
        "usedCookie": tick.used_cookie,
        "checkTickMode": "cookie" if tick.used_cookie else "no_cookie",
        "resolverDebug": {},
    }


def _profile_tick_status(name: str, verified_label: str, reason: str, http_code: int) -> Status:
    if name or verified_label:
        return "LIVE"
    normalized_reason = str(reason or "").lower()
    if "profile_seen_not_verified" in normalized_reason:
        return "LIVE"
    terminal_reasons = (
        "content_unavailable",
        "page_not_found",
        "profile_unavailable",
        "http_404",
    )
    if int(http_code or 0) in {200, 404} and any(marker in normalized_reason for marker in terminal_reasons):
        return "DIE"
    return "UNKNOWN"


def _profile_tick_status_with_uid(raw_input: str, tick: Any, current_status: Status) -> Status:
    if current_status != "UNKNOWN":
        return current_status

    uid = str(tick.uid or "").strip()
    if not uid:
        if _profile_tick_name_miss_is_die(str(getattr(tick, "reason", "") or ""), int(getattr(tick, "http_code", 0) or 0)):
            return "DIE"
        return current_status

    resolved = ResolvedInput(
        input=str(raw_input or "").strip(),
        uid=uid,
        username=str(getattr(tick, "username", "") or "").strip(),
        canonical_url=str(getattr(tick, "canonical_url", "") or f"https://www.facebook.com/profile.php?id={uid}").strip(),
        source="profile_php",
        reason=str(getattr(tick, "reason", "") or "profile_tick_uid_check"),
    )
    live_die = check_live_die(resolved, mode="1")
    if live_die.status in {"LIVE", "DIE"}:
        return live_die.status  # type: ignore[return-value]
    return current_status


def _profile_tick_name_miss_is_die(reason: str, http_code: int) -> bool:
    normalized_reason = str(reason or "").lower()
    return int(http_code or 0) in {200, 404} and any(
        marker in normalized_reason
        for marker in (
            "no_cookie_and_cookie_name_not_found",
            "cookie_name_and_verified_not_found",
            "no_cookie_and_cookie_verified_not_found",
            "cookie_verified_not_found",
        )
    )


def _enrich_direct_profile_url_name(
    raw_input: str,
    resolved: ResolvedInput,
    status: str,
    current_name: str,
    include_name: bool,
) -> str:
    if current_name or not include_name or status != "LIVE" or not resolved.uid:
        return current_name
    raw = str(raw_input or "").strip().lower()
    if "facebook.com/" not in raw or "profile.php" not in raw:
        return current_name
    try:
        tds = resolve_uid_with_tds_api(
            resolved.canonical_url or raw_input,
            timeout=_direct_profile_name_tds_timeout(),
            deadline=_direct_profile_name_tds_deadline(),
        )
    except Exception:
        return current_name
    if tds.uid and str(tds.uid).strip() == str(resolved.uid).strip() and str(tds.name or "").strip():
        return str(tds.name or "").strip()
    return current_name


def _direct_profile_name_tds_timeout() -> float:
    try:
        return max(1.0, min(float(os.getenv("CHECK_DIRECT_PROFILE_NAME_TDS_TIMEOUT_SEC", "5")), 8.0))
    except ValueError:
        return 5.0


def _direct_profile_name_tds_deadline() -> float:
    try:
        return max(1.0, min(float(os.getenv("CHECK_DIRECT_PROFILE_NAME_TDS_DEADLINE_SEC", "8")), 12.0))
    except ValueError:
        return 8.0


def _compare_inputs(req: UidResolverCompareRequest) -> list[str]:
    candidates = list(req.inputs or [])
    if req.input:
        candidates.insert(0, req.input)

    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out[:20]


def _compare_tds_timeout() -> float:
    try:
        return max(0.5, min(float(os.getenv("UID_COMPARE_TDS_TIMEOUT_SEC", "5")), 8.0))
    except ValueError:
        return 5.0


def _compare_tds_deadline() -> float:
    try:
        return max(0.5, min(float(os.getenv("UID_COMPARE_TDS_DEADLINE_SEC", "8")), 12.0))
    except ValueError:
        return 8.0


@contextmanager
def _tds_disabled_for_internal_compare():
    original = facebook_uid_resolver.resolve_uid_with_tds_api

    def fake_tds(*_args, **_kwargs):
        return TdsUidResolution(
            uid="",
            name="",
            source="tds_uid_api",
            reason="tds_api_unavailable_after_deadline",
            http_code=0,
            elapsed_ms=0,
            raw={},
        )

    facebook_uid_resolver.resolve_uid_with_tds_api = fake_tds
    try:
        yield
    finally:
        facebook_uid_resolver.resolve_uid_with_tds_api = original


def _profile_name_lookup_enabled() -> bool:
    disabled = os.getenv("PROFILE_NAME_LOOKUP_DISABLED", "").strip().lower()
    return disabled not in {"1", "true", "yes", "on"}


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
    jobs = list(req.jobs or [])
    results: list[dict[str, Any] | None] = [None] * len(jobs)
    uid_tasks: list[tuple[int, RealtimeBulkJob, str, str]] = []
    post_tasks: list[tuple[int, RealtimeBulkJob, str, str]] = []

    for index, job in enumerate(jobs):
        job_id = (job.id or f"job_{index + 1}").strip()
        job_type = (job.type or "uid").strip().lower()
        if job_type not in {"uid", "post"}:
            results[index] = _realtime_job_error(job_id, job_type, "unsupported_job_type")
            continue

        raw_input = (job.input or job.uid or job.url or "").strip()
        if not raw_input:
            results[index] = _realtime_job_error(job_id, job_type, "empty_input")
            continue

        if job_type == "uid":
            uid_tasks.append((index, job, job_id, raw_input))
            continue

        post_tasks.append((index, job, job_id, raw_input))

    if uid_tasks:
        worker_count = _realtime_bulk_uid_worker_count(len(uid_tasks))
        if worker_count <= 1:
            for index, job, job_id, raw_input in uid_tasks:
                results[index] = _run_realtime_uid_job(job, job_id, raw_input)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(_run_realtime_uid_job, job, job_id, raw_input): index
                    for index, job, job_id, raw_input in uid_tasks
                }
                for future in as_completed(future_map):
                    index = future_map[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:
                        job = jobs[index]
                        job_id = (job.id or f"job_{index + 1}").strip()
                        results[index] = _realtime_job_error(job_id, "uid", f"job_error:{type(exc).__name__}")

    if post_tasks:
        worker_count = _realtime_bulk_post_worker_count(len(post_tasks))
        if worker_count <= 1:
            for index, job, job_id, raw_input in post_tasks:
                results[index] = _run_realtime_post_job(job, job_id, raw_input)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(_run_realtime_post_job, job, job_id, raw_input): index
                    for index, job, job_id, raw_input in post_tasks
                }
                for future in as_completed(future_map):
                    index = future_map[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:
                        job = jobs[index]
                        job_id = (job.id or f"job_{index + 1}").strip()
                        results[index] = _realtime_job_error(job_id, "post", f"job_error:{type(exc).__name__}")

    final_results = [
        item if item is not None else _realtime_job_error(f"job_{index + 1}", "uid", "job_not_processed")
        for index, item in enumerate(results)
    ]

    return {
        "ok": True,
        "results": final_results,
        "jobCount": len(jobs),
        "elapsedMs": int((perf_counter() - started) * 1000),
    }


def _run_realtime_uid_job(job: RealtimeBulkJob, job_id: str, raw_input: str) -> dict[str, Any]:
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
        return item
    except Exception as exc:
        return _realtime_job_error(job_id, "uid", f"job_error:{type(exc).__name__}")


def _run_realtime_post_job(job: RealtimeBulkJob, job_id: str, raw_input: str) -> dict[str, Any]:
    try:
        item = latest_post_input(
            LatestPostRequest(
                input=raw_input,
                uid=job.uid,
                url=job.url,
            )
        )
        item["id"] = job_id
        item["type"] = "post"
        return item
    except Exception as exc:
        return _realtime_job_error(job_id, "post", f"job_error:{type(exc).__name__}")


def _realtime_job_error(job_id: str, job_type: str, reason: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "type": job_type,
        "ok": False,
        "reason": reason,
        "status": "UNKNOWN",
        "uid": "",
        "httpCode": 0,
        "elapsedMs": 0,
    }


def _realtime_bulk_uid_worker_count(job_count: int) -> int:
    try:
        configured = int(os.getenv("REALTIME_BULK_UID_MAX_WORKERS", "10"))
    except ValueError:
        configured = 10
    return max(1, min(job_count, configured))


def _realtime_bulk_post_worker_count(job_count: int) -> int:
    try:
        configured = int(os.getenv("REALTIME_BULK_POST_MAX_WORKERS", "2"))
    except ValueError:
        configured = 2
    return max(1, min(job_count, configured))
