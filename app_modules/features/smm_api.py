from __future__ import annotations

from app_modules.features.viplike import create_viplike_order, get_viplike_packages


def smm_status() -> dict:
    packages = get_viplike_packages(refresh=False, include_raw=False)
    return {
        "ok": True,
        "feature": "viplike_smm_core",
        "packageCount": packages.get("count", 0),
        "source": packages.get("source", ""),
        "apiConfigured": packages.get("apiConfigured", False),
    }
