from __future__ import annotations

from app_modules.checkers.probe_result import ProbeResult


def probe_mode4_external_checker(uid: str) -> ProbeResult:
    return ProbeResult(
        status="UNKNOWN",
        confidence="weak",
        source="mode4_external_checker",
        reason="mode_not_implemented",
        http_code=0,
        details={"uid": str(uid or ""), "mode": "4"},
    )
