from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app_modules.api.controller import (
    CheckRequest,
    LatestPostRequest,
    RealtimeBulkRequest,
    VipLikeOrderRequest,
    check_input,
    check_name_input,
    check_tick_input,
    health_payload,
    latest_post_input,
    realtime_check_bulk,
    viplike_order_input,
    viplike_packages_input,
)
from app_modules.core.config import get_config
from app_modules.telegram.telegram_relay import relay_status, relay_telegram_webhook


app = FastAPI(title="Clean Webhook Checker", version="step16-clean-webhook-checker-local")


def require_api_key(x_api_key: str | None) -> None:
    config = get_config()
    if config.api_key and x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="invalid_api_key")


@app.get("/health")
def health() -> dict:
    return health_payload()


@app.get("/")
def root() -> dict:
    payload = health_payload()
    payload.update(
        {
            "architecture": "clean_combined_render_service",
            "features": [
                "/webhook/telegram",
                "/check",
                "/checktick",
                "/latest-post",
                "/checkpost",
                "/realtime/check-bulk",
                "/viplike/packages",
                "/viplike/order",
            ],
        }
    )
    payload.update(relay_status())
    return payload


@app.post("/check")
def check(req: CheckRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return check_input(req)


@app.post("/checkname")
@app.post("/check-name")
def check_name(req: CheckRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return check_name_input(req)


@app.post("/checktick")
@app.post("/check-tick")
def check_tick(req: CheckRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return check_tick_input(req)


@app.post("/latest-post")
@app.post("/latest-post/")
@app.post("/checkpost")
def latest_post(req: LatestPostRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return latest_post_input(req)


@app.post("/realtime/check-bulk")
@app.post("/realtime/check-bulk/")
def realtime_bulk(req: RealtimeBulkRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return realtime_check_bulk(req)


@app.get("/viplike/packages")
@app.get("/viplike/packages/")
def viplike_packages(
    refresh: bool = False,
    includeRaw: bool = False,
    x_api_key: str | None = Header(default=None),
) -> dict:
    require_api_key(x_api_key)
    return viplike_packages_input(refresh=refresh, include_raw=includeRaw)


@app.post("/viplike/order")
@app.post("/viplike/order/")
def viplike_order(req: VipLikeOrderRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return viplike_order_input(req)


@app.post("/webhook/telegram")
async def webhook_telegram(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    body = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    background_tasks.add_task(relay_telegram_webhook, body or b"{}", content_type)
    return JSONResponse({"ok": True, "queued": True}, status_code=200)
