from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


VerificationState = Literal["VERIFIED", "NOT_VERIFIED", "UNKNOWN"]
ProfileState = Literal["VISIBLE", "AUTH_WALL", "UNAVAILABLE", "CHECKPOINT", "NETWORK_ERROR", "UNKNOWN"]


@dataclass(frozen=True)
class VerificationProbe:
    source: str
    url: str
    final_url: str
    http_code: int
    profile_state: ProfileState
    verification_state: VerificationState
    reason: str
    elapsed_ms: int
    bytes_read: int
    complete: bool
    used_cookie: bool
    cookie_account: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "source": self.source,
            "url": self.url,
            "finalUrl": self.final_url,
            "httpCode": self.http_code,
            "profileState": self.profile_state,
            "verificationState": self.verification_state,
            "reason": self.reason,
            "elapsedMs": self.elapsed_ms,
            "bytesRead": self.bytes_read,
            "complete": self.complete,
            "usedCookie": self.used_cookie,
        }
        if self.cookie_account:
            data["cookieAccount"] = self.cookie_account
        return data


@dataclass(frozen=True)
class ProfileVerificationResult:
    verification_state: VerificationState
    profile_state: ProfileState
    conclusive: bool
    uid: str
    username: str
    canonical_url: str
    source: str
    reason: str
    http_code: int
    elapsed_ms: int
    probes: list[VerificationProbe] = field(default_factory=list)
    used_cookie: bool = False

    @property
    def verified(self) -> bool:
        return self.verification_state == "VERIFIED"

    def to_dict(self) -> dict[str, object]:
        return {
            "verificationState": self.verification_state,
            "profileState": self.profile_state,
            "verificationKnown": self.conclusive,
            "verified": self.verified,
            "isVerified": self.verified,
            "verifiedLabel": "Tài khoản đã xác minh" if self.verified else "",
            "uid": self.uid,
            "username": self.username,
            "canonicalUrl": self.canonical_url,
            "source": self.source,
            "reason": self.reason,
            "httpCode": self.http_code,
            "elapsedMs": self.elapsed_ms,
            "probes": [probe.to_dict() for probe in self.probes],
            "usedCookie": self.used_cookie,
        }
