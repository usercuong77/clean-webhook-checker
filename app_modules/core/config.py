import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ServiceConfig:
    app_name: str
    version: str
    api_key: str
    request_timeout_seconds: float
    telegram_relay_target_url: str
    telegram_relay_timeout_seconds: float
    webhook_shared_secret: str
    smm_api_base_url: str
    smm_api_key: str
    smm_api_timeout_seconds: float
    viplike_order_enabled: bool


def _first_env(keys: tuple[str, ...]) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _env_flag(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


@lru_cache(maxsize=1)
def get_config() -> ServiceConfig:
    return ServiceConfig(
        app_name=os.getenv("APP_NAME", "clean-webhook-checker"),
        version=os.getenv("APP_VERSION", "step27-1-viplike-smm-core"),
        api_key=_first_env(
            (
                "UID_CHECKER_API_KEY",
                "EXTERNAL_CHECKER_API_KEY",
                "BOT_NEW_CHECKER_API_KEY",
                "CHECKER_API_KEY",
                "FB_UID_API_KEY",
            )
        ),
        request_timeout_seconds=float(os.getenv("UID_CHECKER_TIMEOUT", "10")),
        telegram_relay_target_url=os.getenv("TELEGRAM_RELAY_TARGET_URL", "").strip(),
        telegram_relay_timeout_seconds=float(os.getenv("TELEGRAM_RELAY_TIMEOUT_SEC", "25")),
        webhook_shared_secret=os.getenv("WEBHOOK_SHARED_SECRET", "").strip(),
        smm_api_base_url=_first_env(("SMM_API_BASE_URL", "SMM_API_DOMAIN", "VIPLIKE_SMM_API_BASE_URL")),
        smm_api_key=_first_env(("SMM_API_KEY", "VIPLIKE_SMM_API_KEY")),
        smm_api_timeout_seconds=float(os.getenv("SMM_API_TIMEOUT_SEC", os.getenv("SMM_API_TIMEOUT", "6"))),
        viplike_order_enabled=_env_flag("VIPLIKE_ORDER_ENABLED", False),
    )
