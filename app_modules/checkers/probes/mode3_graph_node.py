from __future__ import annotations

from app_modules.checkers.probe_result import ProbeResult


def probe_mode3_graph_node(uid: str) -> ProbeResult:
    return ProbeResult(
        status="UNKNOWN",
        confidence="weak",
        source="mode3_graph_node",
        reason="mode_not_implemented",
        http_code=0,
        details={"uid": str(uid or ""), "mode": "3"},
    )
