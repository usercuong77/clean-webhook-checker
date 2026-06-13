"""Compatibility alias for the profile tick implementation.

Check-tick code lives in app_modules.features.profile_tick. This facade keeps
older imports working while making the file ownership clear.
"""

from __future__ import annotations

import sys

from app_modules.features import profile_tick as _profile_tick

sys.modules[__name__] = _profile_tick
