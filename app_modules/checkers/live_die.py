from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_modules.checkers.check_modes import MODE_CONFIGS, dispatch_mode, normalize_mode
from app_modules.checkers.probe_result import ProbeResult
from app_modules.resolvers.uid_resolver import ResolvedInput


@dataclass(frozen=True)
class LiveDieResult:
    status: str
    confidence: str
    source: str
    reason: str
    http_code: int
    probes: list[dict[str, Any]]


def check_live_die(resolved: ResolvedInput, mode: str | None = "all") -> LiveDieResult:
    normalized_mode = normalize_mode(mode)

    if not resolved.input:
        return LiveDieResult(
            status="DIE",
            confidence="weak",
            source="input",
            reason="empty_input",
            http_code=0,
            probes=[],
        )

    if not resolved.uid and not resolved.username:
        return LiveDieResult(
            status="DIE",
            confidence="weak",
            source=resolved.source,
            reason=resolved.reason or "input_not_resolved",
            http_code=0,
            probes=[
                {
                    "source": resolved.source,
                    "status": "DIE",
                    "reason": resolved.reason or "input_not_resolved",
                    "requestedMode": normalized_mode,
                }
            ],
        )

    resolver_probe = {
        "source": resolved.source,
        "status": "resolved" if resolved.uid else "partial",
        "uid": resolved.uid,
        "username": resolved.username,
        "canonicalUrl": resolved.canonical_url,
        "reason": resolved.reason,
        "needsNetworkResolve": resolved.needs_network_resolve,
        "resolverProbes": resolved.resolver_probes,
        "requestedMode": normalized_mode,
    }

    if not resolved.uid:
        return LiveDieResult(
            status="DIE",
            confidence="weak",
            source=resolved.source,
            reason=resolved.reason or "uid_not_resolved",
            http_code=0,
            probes=[resolver_probe],
        )

    requested_mode, probe = dispatch_mode(resolved.uid, normalized_mode)
    executed_mode = "1" if requested_mode == "all" else requested_mode
    reason = probe.reason if requested_mode != "all" else f"all_currently_mode1_only:{probe.reason}"
    return _from_probe_result(probe, reason, [resolver_probe], executed_mode, requested_mode)


def _from_probe_result(
    probe: ProbeResult,
    reason: str,
    previous_probes: list[dict[str, Any]],
    mode: str,
    requested_mode: str,
) -> LiveDieResult:
    mode_config = MODE_CONFIGS.get(mode)
    return LiveDieResult(
        status=probe.status,
        confidence=probe.confidence,
        source=probe.source,
        reason=reason,
        http_code=probe.http_code,
        probes=previous_probes
        + [
            {
                "source": probe.source,
                "status": probe.status,
                "confidence": probe.confidence,
                "reason": probe.reason,
                "httpCode": probe.http_code,
                "mode": mode,
                "requestedMode": requested_mode,
                "implemented": bool(mode_config.implemented) if mode_config else False,
                "details": probe.details,
            }
        ],
    )
