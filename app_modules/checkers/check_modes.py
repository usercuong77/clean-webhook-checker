from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app_modules.checkers.probe_result import ProbeResult
from app_modules.checkers.probes.mode1_graph_public import probe_mode1_graph_public
from app_modules.checkers.probes.mode2_graph_app_token import probe_mode2_graph_app_token
from app_modules.checkers.probes.mode3_graph_node import probe_mode3_graph_node
from app_modules.checkers.probes.mode4_external_checker import probe_mode4_external_checker
from app_modules.checkers.probes.mode5_html_fallback import probe_mode5_html_fallback


@dataclass(frozen=True)
class ModeConfig:
    mode: str
    source: str
    description: str
    implemented: bool
    handler: Callable[[str], ProbeResult]


MODE_CONFIGS: dict[str, ModeConfig] = {
    "1": ModeConfig(
        mode="1",
        source="mode1_graph_public",
        description="Graph public picture height/width rule",
        implemented=True,
        handler=probe_mode1_graph_public,
    ),
    "2": ModeConfig(
        mode="2",
        source="mode2_graph_app_token",
        description="Graph picture with app token",
        implemented=False,
        handler=probe_mode2_graph_app_token,
    ),
    "3": ModeConfig(
        mode="3",
        source="mode3_graph_node",
        description="Graph/node/profile-name signal",
        implemented=False,
        handler=probe_mode3_graph_node,
    ),
    "4": ModeConfig(
        mode="4",
        source="mode4_external_checker",
        description="External checker adapter",
        implemented=False,
        handler=probe_mode4_external_checker,
    ),
    "5": ModeConfig(
        mode="5",
        source="mode5_html_fallback",
        description="HTML/mobile/mbasic fallback",
        implemented=False,
        handler=probe_mode5_html_fallback,
    ),
}

SUPPORTED_MODES = frozenset([*MODE_CONFIGS.keys(), "all"])


def normalize_mode(mode: str | None) -> str:
    value = str(mode or "1").strip().lower()
    return value if value in SUPPORTED_MODES else "1"


def dispatch_mode(uid: str, mode: str | None) -> tuple[str, ProbeResult]:
    normalized_mode = normalize_mode(mode)
    selected_mode = "1" if normalized_mode == "all" else normalized_mode
    config = MODE_CONFIGS[selected_mode]
    return normalized_mode, config.handler(uid)
