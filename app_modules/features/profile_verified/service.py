from __future__ import annotations

import os
import time

import requests

from app_modules.features.facebook_profile_fetch import cookie_headers, public_headers
from app_modules.resolvers.facebook_cookies import load_cookie_accounts
from app_modules.resolvers.fb_uid_lite_adapter import resolve_uid_with_lite_sync

from .input_normalizer import (
    ProfileTarget,
    cookie_candidate_urls,
    normalize_profile_target,
    public_candidate_urls,
    retarget_profile,
)
from .models import ProfileVerificationResult, VerificationProbe
from .parser import STOP_MARKERS, ParsedVerification, parse_profile_document
from .probes import ProbeDocument, fetch_profile_document


def check_profile_verification(raw_input: str, force_cookie: bool = False) -> ProfileVerificationResult:
    started = time.perf_counter()
    target = normalize_profile_target(raw_input)
    probes: list[VerificationProbe] = []
    best: tuple[ParsedVerification, ProfileTarget, ProbeDocument, str, bool] | None = None

    if not target.normalized_url:
        return _result(started, target, probes, None, "invalid_input", False)

    if not force_cookie:
        for url in public_candidate_urls(target)[:_public_probe_limit()]:
            document = fetch_profile_document(
                url,
                public_headers(),
                timeout=_public_timeout(),
                max_bytes=_public_read_cap(),
                stop_markers=STOP_MARKERS,
            )
            parsed = parse_profile_document(
                document.text,
                document.final_url,
                document.http_code,
                target.uid,
                target.username,
                document.complete,
                allow_not_verified=False,
            )
            target = retarget_profile(target, document.final_url)
            probes.append(_probe("profile_verified_v2_public", url, document, parsed, False))
            best = _prefer(best, parsed, target, document, "profile_verified_v2_public", False)
            if parsed.conclusive:
                return _result(started, target, probes, best, parsed.reason, False)
            if parsed.profile_state in {"AUTH_WALL", "CHECKPOINT", "NETWORK_ERROR"}:
                break

    target = _resolve_target_uid(target)
    accounts = [account for account in load_cookie_accounts() if account.is_usable][:_cookie_account_limit(force_cookie)]
    if not accounts:
        return _result(started, target, probes, best, "cookie_account_unavailable", False)

    deadline = time.perf_counter() + _cookie_total_deadline(force_cookie)
    for account in accounts:
        with requests.Session() as session:
            pending_urls = cookie_candidate_urls(target)
            attempted_urls: set[str] = set()
            for _ in range(_cookie_probe_limit(force_cookie)):
                url = next((item for item in pending_urls if item not in attempted_urls), "")
                if not url:
                    break
                attempted_urls.add(url)
                if time.perf_counter() >= deadline:
                    return _result(started, target, probes, best, "cookie_deadline_reached", True)
                document = fetch_profile_document(
                    url,
                    cookie_headers(account),
                    timeout=_cookie_timeout(force_cookie),
                    max_bytes=_cookie_read_cap(),
                    stop_markers=STOP_MARKERS,
                    session=session,
                )
                parsed = parse_profile_document(
                    document.text,
                    document.final_url,
                    document.http_code,
                    target.uid,
                    target.username,
                    document.complete,
                    allow_not_verified=True,
                )
                updated_target = retarget_profile(target, document.final_url)
                if updated_target != target:
                    target = updated_target
                    pending_urls = cookie_candidate_urls(target) + pending_urls
                probes.append(_probe(
                    "profile_verified_v2_cookie",
                    url,
                    document,
                    parsed,
                    True,
                    account.masked_id,
                ))
                best = _prefer(best, parsed, target, document, "profile_verified_v2_cookie", True)
                if parsed.conclusive:
                    return _result(started, target, probes, best, parsed.reason, True)
                if parsed.profile_state == "UNAVAILABLE":
                    break

    return _result(started, target, probes, best, "verification_inconclusive", bool(accounts))


def _probe(
    source: str,
    url: str,
    document: ProbeDocument,
    parsed: ParsedVerification,
    used_cookie: bool,
    cookie_account: str = "",
) -> VerificationProbe:
    return VerificationProbe(
        source=source,
        url=url,
        final_url=document.final_url,
        http_code=document.http_code,
        profile_state=parsed.profile_state,
        verification_state=parsed.verification_state,
        reason=parsed.reason if document.http_code else document.reason,
        elapsed_ms=document.elapsed_ms,
        bytes_read=document.bytes_read,
        complete=document.complete,
        used_cookie=used_cookie,
        cookie_account=cookie_account,
    )


def _resolve_target_uid(target: ProfileTarget) -> ProfileTarget:
    if target.uid or not target.normalized_url:
        return target
    try:
        resolved = resolve_uid_with_lite_sync(target.normalized_url)
    except Exception:
        return target
    uid = str(getattr(resolved, "uid", "") or "").strip()
    if not getattr(resolved, "ok", False) or not uid.isdigit():
        return target
    return ProfileTarget(
        raw_input=target.raw_input,
        normalized_url=f"https://www.facebook.com/profile.php?id={uid}",
        uid=uid,
        username=target.username,
        canonical_url=f"https://www.facebook.com/profile.php?id={uid}",
    )


def _prefer(
    current: tuple[ParsedVerification, ProfileTarget, ProbeDocument, str, bool] | None,
    parsed: ParsedVerification,
    target: ProfileTarget,
    document: ProbeDocument,
    source: str,
    used_cookie: bool,
) -> tuple[ParsedVerification, ProfileTarget, ProbeDocument, str, bool]:
    candidate = (parsed, target, document, source, used_cookie)
    if current is None:
        return candidate
    current_parsed = current[0]
    score = _evidence_score(parsed)
    current_score = _evidence_score(current_parsed)
    return candidate if score >= current_score else current


def _evidence_score(parsed: ParsedVerification) -> int:
    if parsed.verification_state == "VERIFIED":
        return 100
    if parsed.verification_state == "NOT_VERIFIED" and parsed.conclusive:
        return 90
    return {
        "VISIBLE": 60,
        "UNAVAILABLE": 50,
        "AUTH_WALL": 30,
        "CHECKPOINT": 20,
        "NETWORK_ERROR": 10,
        "UNKNOWN": 0,
    }.get(parsed.profile_state, 0)


def _result(
    started: float,
    fallback_target: ProfileTarget,
    probes: list[VerificationProbe],
    best: tuple[ParsedVerification, ProfileTarget, ProbeDocument, str, bool] | None,
    fallback_reason: str,
    used_cookie: bool,
) -> ProfileVerificationResult:
    if best is None:
        return ProfileVerificationResult(
            verification_state="UNKNOWN",
            profile_state="UNKNOWN",
            conclusive=False,
            uid=fallback_target.uid,
            username=fallback_target.username,
            canonical_url=fallback_target.canonical_url,
            source="profile_verified_v2",
            reason=fallback_reason,
            http_code=probes[-1].http_code if probes else 0,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            probes=probes,
            used_cookie=used_cookie,
        )
    parsed, target, document, source, selected_used_cookie = best
    return ProfileVerificationResult(
        verification_state=parsed.verification_state,
        profile_state=parsed.profile_state,
        conclusive=parsed.conclusive,
        uid=target.uid,
        username=target.username,
        canonical_url=target.canonical_url,
        source=source,
        reason=parsed.reason if parsed.reason else fallback_reason,
        http_code=document.http_code,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        probes=probes,
        used_cookie=selected_used_cookie or used_cookie,
    )


def _public_timeout() -> float:
    return _float_env("PROFILE_VERIFIED_V2_PUBLIC_TIMEOUT_SEC", 1.6, 0.7, 3.0)


def _cookie_timeout(force_cookie: bool) -> float:
    default = 2.6 if force_cookie else 2.2
    return _float_env("PROFILE_VERIFIED_V2_COOKIE_TIMEOUT_SEC", default, 0.9, 4.0)


def _cookie_total_deadline(force_cookie: bool) -> float:
    default = 9.0 if force_cookie else 7.5
    return _float_env("PROFILE_VERIFIED_V2_COOKIE_DEADLINE_SEC", default, 2.0, 12.0)


def _public_read_cap() -> int:
    return _int_env("PROFILE_VERIFIED_V2_PUBLIC_READ_CAP_BYTES", 900_000, 200_000, 1_800_000)


def _cookie_read_cap() -> int:
    return _int_env("PROFILE_VERIFIED_V2_COOKIE_READ_CAP_BYTES", 1_400_000, 300_000, 3_600_000)


def _public_probe_limit() -> int:
    return _int_env("PROFILE_VERIFIED_V2_PUBLIC_PROBE_LIMIT", 1, 1, 2)


def _cookie_probe_limit(force_cookie: bool) -> int:
    return _int_env("PROFILE_VERIFIED_V2_COOKIE_PROBE_LIMIT", 2, 1, 3)


def _cookie_account_limit(force_cookie: bool) -> int:
    return _int_env("PROFILE_VERIFIED_V2_COOKIE_ACCOUNT_LIMIT", 2, 1, 4)


def _float_env(key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(os.getenv(key, str(default))), maximum))
    except ValueError:
        return default


def _int_env(key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.getenv(key, str(default))), maximum))
    except ValueError:
        return default
