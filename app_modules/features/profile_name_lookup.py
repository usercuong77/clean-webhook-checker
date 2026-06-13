"""Compatibility alias for the fast profile-name resolver.

New code should import from app_modules.features.profile_name_resolver.
"""

from __future__ import annotations

import sys

from app_modules.features import profile_name_resolver as _profile_name_resolver

sys.modules[__name__] = _profile_name_resolver
