from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProbeResult:
    status: str
    confidence: str
    source: str
    reason: str
    http_code: int
    details: dict[str, Any]
