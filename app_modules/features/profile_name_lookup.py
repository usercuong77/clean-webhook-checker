"""Name-only profile lookup facade.

This module intentionally exposes only the lightweight profile-name path used by
/check and /add. Keep checktick/verified-account logic in profile_name.py so the
two flows can evolve independently.
"""

from app_modules.features.profile_name import (
    ProfileNameResult,
    build_profile_name_urls,
    choose_profile_name,
    clear_profile_name_cache,
    extract_profile_name,
    is_valid_profile_name,
    resolve_profile_name,
)

__all__ = [
    "ProfileNameResult",
    "build_profile_name_urls",
    "choose_profile_name",
    "clear_profile_name_cache",
    "extract_profile_name",
    "is_valid_profile_name",
    "resolve_profile_name",
]
