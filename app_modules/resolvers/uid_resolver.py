from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app_modules.resolvers.facebook_uid_resolver import resolve_uid_from_any_input


@dataclass(frozen=True)
class ResolvedInput:
    input: str
    uid: str
    username: str
    canonical_url: str
    source: str
    reason: str
    needs_network_resolve: bool = False
    resolver_probes: list[dict[str, Any]] = field(default_factory=list)


def resolve_input(raw: str) -> ResolvedInput:
    resolved = resolve_uid_from_any_input(raw)
    return ResolvedInput(
        input=resolved.input,
        uid=resolved.uid,
        username=resolved.username,
        canonical_url=resolved.canonical_url,
        source=resolved.source,
        reason=resolved.reason,
        needs_network_resolve=bool(resolved.probes and not resolved.uid),
        resolver_probes=resolved.probes,
    )
