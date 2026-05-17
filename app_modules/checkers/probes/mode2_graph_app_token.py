from __future__ import annotations

from app_modules.checkers.probe_result import ProbeResult


def probe_mode2_graph_app_token(uid: str) -> ProbeResult:
    return ProbeResult(
        status="UNKNOWN",
        confidence="weak",
        source="mode2_graph_app_token",
        reason="mode_not_implemented",
        http_code=0,
        details={"uid": str(uid or ""), "mode": "2"},
    )
