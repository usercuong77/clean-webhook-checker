"""Compatibility alias for the profile tick implementation.

New code should import from app_modules.features.profile_tick. This alias keeps
older tests/tools working while we finish the module split.
"""

from __future__ import annotations

import sys

from app_modules.features import profile_tick as _profile_tick

sys.modules[__name__] = _profile_tick
