from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
import requests

from app_modules.api.controller import (
    CheckRequest,
    LatestPostRequest,
    RealtimeBulkRequest,
    check_input,
    health_payload,
    latest_post_input,
    realtime_check_bulk,
)
from app_modules.core.config import get_config
from app_modules.telegram.telegram_relay import build_relay_target_url, relay_status, relay_telegram_webhook


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
                "/latest-post",
                "/checkpost",
                "/realtime/check-bulk",
            ],
        }
    )
    payload.update(relay_status())
    return payload


@app.post("/check")
def check(req: CheckRequest, x_api_key: str | None = Header(default=None)) -> dict:
    require_api_key(x_api_key)
    return check_input(req)


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


@app.post("/webhook/telegram")
async def webhook_telegram(request: Request) -> JSONResponse:
    body = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    result = relay_telegram_webhook(body or b"{}", content_type)
    status_code = int(result.pop("statusCode", 200))
    return JSONResponse(result, status_code=status_code)


@app.post("/admin/apps-script-cutover")
async def admin_apps_script_cutover(request: Request) -> JSONResponse:
    payload = await request.json()
    action = str(payload.get("action", "")).strip()
    if action not in {"snapshot", "apply", "set_telegram_webhook"}:
        raise HTTPException(status_code=400, detail="invalid_cutover_action")

    target_url = build_relay_target_url()
    if not target_url:
        raise HTTPException(status_code=500, detail="telegram_relay_target_missing")

    upstream = requests.post(
        target_url,
        json={"clean_cutover_action": action},
        timeout=30,
        allow_redirects=True,
    )
    try:
        upstream_body = upstream.json()
    except ValueError:
        upstream_body = {"rawBody": upstream.text[:2000]}

    return JSONResponse(
        {
            "ok": 200 <= upstream.status_code < 300,
            "upstreamStatus": upstream.status_code,
            "upstream": upstream_body,
        },
        status_code=200 if 200 <= upstream.status_code < 300 else 502,
    )
