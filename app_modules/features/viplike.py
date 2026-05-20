from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests

from app_modules.core.config import get_config


DEFAULT_SMM_API_BASE_URL = "https://api.baostar.pro/api/v2"
DEFAULT_FACEBOOK_LIKE_ENDPOINT = "/api/facebook-like-gia-re/buy"
DEFAULT_SMM_PACKAGE_TIMEOUT_SECONDS = 6.0
DEFAULT_SMM_PACKAGE_REFRESH_TIMEOUT_SECONDS = 25.0
MAX_SMM_PACKAGE_REFRESH_TIMEOUT_SECONDS = 45.0
DEFAULT_SMM_ORDER_TIMEOUT_SECONDS = 25.0
MAX_SMM_ORDER_TIMEOUT_SECONDS = 45.0
VALID_REACTIONS = ("like", "love", "care", "haha", "wow", "sad", "angry")
CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES = (
    "facebook_like",
    "facebook_like_v3",
    "facebook_like_v7",
    "facebook_like_v8",
    "facebook_like_v10",
    "facebook_like_v33",
    "facebook_like_v34",
    "facebook_like_v401",
    "facebook_like_v402",
)
CANONICAL_FACEBOOK_LIKE_PACKAGE_SET = set(CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES)
CANONICAL_FACEBOOK_LIKE_PACKAGE_ORDER = {
    name: index for index, name in enumerate(CANONICAL_FACEBOOK_LIKE_PACKAGE_NAMES)
}
REACTION_PACKAGE_KEYS = {
    "facebook like v401",
    "facebook like v3",
    "facebook like v33",
    "facebook like v34",
    "facebook like v7",
    "facebook like v402",
}


@dataclass(frozen=True)
class SmmApiResult:
    ok: bool
    http_status: int
    url: str
    json: dict[str, Any] | list[Any] | None
    body: str
    error: str
    elapsed_ms: int


def get_viplike_packages(refresh: bool = False, include_raw: bool = False) -> dict[str, Any]:
    started = perf_counter()
    config = get_config()
    api_configured = bool(config.smm_api_key)
    api_result: SmmApiResult | None = None
    packages: list[dict[str, Any]] = []
    reason = ""
    source = ""

    if api_configured:
        api_result = call_smm_api("GET", "/api/prices", timeout_seconds=smm_package_timeout_seconds(refresh=refresh))
        if api_result.ok and isinstance(api_result.json, Mapping):
            rows = build_smm_package_rows(api_result.json)
            packages = normalize_viplike_package_rows(rows)
            source = "api"
            if not packages:
                reason = "api_returned_no_facebook_like_packages"
        else:
            reason = api_result.error or "smm_api_failed"

    if not packages:
        packages = fallback_viplike_packages()
        source = "fallback_smm_api_key_missing" if not api_configured else "fallback_api_failed"
        if not reason:
            reason = "smm_api_key_missing" if not api_configured else "smm_api_failed"

    out: dict[str, Any] = {
        "ok": True,
        "packages": packages,
        "count": len(packages),
        "source": source,
        "reason": reason or "ok",
        "apiConfigured": api_configured,
        "refresh": bool(refresh),
        "elapsedMs": int((perf_counter() - started) * 1000),
    }
    if api_result:
        out["apiHttpStatus"] = api_result.http_status
        out["apiUrl"] = api_result.url
        out["apiError"] = api_result.error
        if include_raw:
            out["raw"] = api_result.json
    return out


def create_viplike_order(request: Mapping[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    config = get_config()
    payload_result = build_viplike_order_payload(request)
    if not payload_result["ok"]:
        payload_result["elapsedMs"] = int((perf_counter() - started) * 1000)
        return payload_result

    confirm = bool(request.get("confirm"))
    dry_run = bool(request.get("dryRun") or request.get("dry_run") or not confirm)
    if dry_run:
        payload_result.update(
            {
                "ok": True,
                "created": False,
                "dryRun": True,
                "reason": "dry_run",
                "elapsedMs": int((perf_counter() - started) * 1000),
            }
        )
        return payload_result

    if not config.viplike_order_enabled:
        payload_result.update(
            {
                "ok": False,
                "created": False,
                "dryRun": False,
                "reason": "viplike_order_disabled",
                "message": "VIPLIKE order creation is disabled by VIPLIKE_ORDER_ENABLED.",
                "elapsedMs": int((perf_counter() - started) * 1000),
            }
        )
        return payload_result

    api_result = call_smm_api(
        "POST",
        payload_result["endpoint"],
        payload_result["payload"],
        timeout_seconds=smm_order_timeout_seconds(),
    )
    order_id = extract_smm_order_id(api_result.json)
    message = pick_smm_api_message(api_result.json, "Tao don thanh cong." if api_result.ok else api_result.error)
    payload_result.update(
        {
            "ok": bool(api_result.ok),
            "created": bool(api_result.ok),
            "dryRun": False,
            "reason": "ok" if api_result.ok else "viplike_smm_order_failed",
            "orderId": order_id,
            "httpStatus": api_result.http_status,
            "apiUrl": api_result.url,
            "error": "" if api_result.ok else api_result.error,
            "message": message,
            "elapsedMs": int((perf_counter() - started) * 1000),
        }
    )
    return payload_result


def build_viplike_order_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    uid = clean_text(request.get("uid"))
    object_id = clean_text(
        request.get("objectId")
        or request.get("object_id")
        or request.get("postId")
        or request.get("post_id")
    )
    post_link = clean_text(request.get("postLink") or request.get("post_link") or request.get("link"))
    if not object_id:
        object_id = post_link
    if not object_id:
        return {"ok": False, "reason": "missing_post_object_id"}

    quantity = int_number(request.get("quantity"))
    if quantity < 1:
        return {"ok": False, "reason": "invalid_quantity"}

    package_name = normalize_viplike_package_name(request.get("packageName") or request.get("package_name"))
    if not package_name:
        return {"ok": False, "reason": "missing_package_name"}

    endpoint = clean_text(request.get("endpoint") or DEFAULT_FACEBOOK_LIKE_ENDPOINT)
    if not endpoint:
        return {"ok": False, "reason": "missing_endpoint"}

    reactions = normalize_reaction_types(
        request.get("reactionTypes")
        or request.get("reaction_types")
        or request.get("reactions")
        or request.get("reactionType")
        or request.get("reaction_type")
        or "like"
    )
    payload = {
        "object_id": object_id,
        "quantity": quantity,
        "package_name": package_name,
        "object_type": "|".join(reactions),
    }
    dedupe_key = clean_text(request.get("dedupeKey") or request.get("dedupe_key"))
    if not dedupe_key:
        dedupe_key = build_viplike_order_dedupe_key(
            {
                "uid": uid,
                "postId": object_id,
                "packageName": package_name,
                "quantity": quantity,
                "reactionTypes": reactions,
            }
        )

    return {
        "ok": True,
        "uid": uid,
        "objectId": object_id,
        "postLink": post_link,
        "packageName": package_name,
        "quantity": quantity,
        "reactionTypes": reactions,
        "endpoint": endpoint,
        "payload": payload,
        "dedupeKey": dedupe_key,
        "dedupeHash": sha256_short(dedupe_key),
        "source": "viplike",
    }


def call_smm_api(
    method: str,
    endpoint: str,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> SmmApiResult:
    started = perf_counter()
    config = get_config()
    if not config.smm_api_key:
        return SmmApiResult(False, 0, "", None, "", "smm_api_key_missing", 0)

    urls = build_smm_request_urls(endpoint)
    if not urls:
        return SmmApiResult(False, 0, "", None, "", "smm_endpoint_invalid", 0)

    last_result: SmmApiResult | None = None
    method_upper = clean_text(method or "POST").upper()
    request_timeout = timeout_seconds if timeout_seconds is not None else config.smm_api_timeout_seconds
    for index, url in enumerate(urls):
        try:
            if method_upper == "GET":
                response = requests.get(
                    url,
                    headers={"api-key": config.smm_api_key},
                    timeout=request_timeout,
                    allow_redirects=True,
                )
            else:
                response = requests.post(
                    url,
                    headers={"api-key": config.smm_api_key},
                    json=dict(payload or {}),
                    timeout=request_timeout,
                    allow_redirects=True,
                )
            body = response.text or ""
            parsed = parse_json_maybe(body)
            api_success = not isinstance(parsed, Mapping) or parsed.get("success") is not False
            ok = 200 <= int(response.status_code) < 300 and api_success
            last_result = SmmApiResult(
                ok=ok,
                http_status=int(response.status_code),
                url=url,
                json=parsed,
                body=body,
                error="" if ok else pick_smm_api_message(parsed, body or "smm_api_failed"),
                elapsed_ms=int((perf_counter() - started) * 1000),
            )
            if response.status_code == 404 and index < len(urls) - 1:
                continue
            return last_result
        except requests.RequestException as exc:
            last_result = SmmApiResult(
                ok=False,
                http_status=0,
                url=url,
                json=None,
                body="",
                error=f"smm_request_error:{type(exc).__name__}",
                elapsed_ms=int((perf_counter() - started) * 1000),
            )
            if index < len(urls) - 1:
                continue
            return last_result

    return last_result or SmmApiResult(False, 0, "", None, "", "smm_api_failed", 0)


def smm_package_timeout_seconds(refresh: bool = False) -> float:
    config = get_config()
    if refresh:
        configured = (
            env_float("SMM_PACKAGE_REFRESH_TIMEOUT_SEC")
            or env_float("VIPLIKE_PACKAGE_REFRESH_TIMEOUT_SEC")
            or max(float(config.smm_api_timeout_seconds or 0), DEFAULT_SMM_PACKAGE_REFRESH_TIMEOUT_SECONDS)
        )
        return max(DEFAULT_SMM_PACKAGE_TIMEOUT_SECONDS, min(configured, MAX_SMM_PACKAGE_REFRESH_TIMEOUT_SECONDS))
    configured = env_float("SMM_PACKAGE_TIMEOUT_SEC") or float(config.smm_api_timeout_seconds or DEFAULT_SMM_PACKAGE_TIMEOUT_SECONDS)
    return max(1.0, min(configured, DEFAULT_SMM_PACKAGE_TIMEOUT_SECONDS))


def smm_order_timeout_seconds() -> float:
    config = get_config()
    configured = (
        env_float("SMM_ORDER_TIMEOUT_SEC")
        or env_float("VIPLIKE_ORDER_TIMEOUT_SEC")
        or max(float(config.smm_api_timeout_seconds or 0), DEFAULT_SMM_ORDER_TIMEOUT_SECONDS)
    )
    return max(DEFAULT_SMM_PACKAGE_TIMEOUT_SECONDS, min(configured, MAX_SMM_ORDER_TIMEOUT_SECONDS))


def env_float(name: str) -> float:
    try:
        return float(os.getenv(name, "0") or "0")
    except ValueError:
        return 0.0


def build_smm_request_urls(endpoint: str) -> list[str]:
    endpoint_clean = clean_text(endpoint)
    if not endpoint_clean:
        return []
    if endpoint_clean.startswith(("http://", "https://")):
        return [endpoint_clean]
    if not endpoint_clean.startswith("/"):
        endpoint_clean = "/" + endpoint_clean

    urls: list[str] = []
    for base in smm_domain_candidates():
        if endpoint_clean.startswith("/api/") and re.search(r"/api(?:/v2)?$", base.rstrip("/"), flags=re.I):
            continue
        append_unique(urls, base.rstrip("/") + endpoint_clean)
    return urls


def smm_domain_candidates() -> list[str]:
    config = get_config()
    domain = clean_text(config.smm_api_base_url or DEFAULT_SMM_API_BASE_URL).rstrip("/")
    if domain and not domain.startswith(("http://", "https://")):
        domain = "https://" + domain

    parsed = urlsplit(domain)
    if not parsed.scheme or not parsed.netloc:
        return []

    root = domain
    root = re.sub(r"/api/v2$", "", root, flags=re.I)
    root = re.sub(r"/api$", "", root, flags=re.I)
    root = root.rstrip("/")

    out: list[str] = []
    append_unique(out, root)
    append_unique(out, domain)
    append_unique(out, root + "/api/v2")
    return out


def build_smm_package_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("data")
    if not isinstance(groups, list):
        return []

    rows: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        service_name = clean_text(group.get("name"))
        path = clean_text(group.get("path"))
        url_api = clean_text(group.get("url_api") or group.get("urlApi"))
        packages = group.get("package")
        if not isinstance(packages, list):
            continue
        for package in packages:
            if not isinstance(package, Mapping):
                continue
            package_name = normalize_viplike_package_name(package.get("package_name") or package.get("packageName"))
            package_title = clean_text(package.get("name") or package.get("title") or package_name)
            haystack = " ".join([service_name, path, url_api, package_name, package_title])
            row = {
                "serviceName": service_name,
                "path": path,
                "urlApi": url_api,
                "packageId": clean_text(package.get("id")),
                "packageName": package_name,
                "packageTitle": package_title,
                "min": int_number(package.get("min")),
                "max": int_number(package.get("max")),
                "pricePer": normalize_price(package.get("price_per") or package.get("pricePer")),
                "actionHint": detect_smm_action(haystack),
                "targetHint": detect_smm_target(haystack),
            }
            row["supportsReactionChoice"] = row_supports_reaction_choice(row)
            rows.append(row)
    return rows


def normalize_viplike_package_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        package_name = normalize_viplike_package_name(row.get("packageName"))
        if package_name not in CANONICAL_FACEBOOK_LIKE_PACKAGE_SET:
            continue
        if clean_text(row.get("targetHint")) != "fb":
            continue
        normalized = normalize_viplike_package_option(
            {
                "id": row.get("packageId") or f"api_fb_like_{index}",
                "label": row.get("packageTitle") or row.get("packageName"),
                "packageName": package_name,
                "endpoint": row.get("urlApi") or row.get("path") or DEFAULT_FACEBOOK_LIKE_ENDPOINT,
                "min": row.get("min"),
                "max": row.get("max"),
                "pricePer": row.get("pricePer"),
                "basePricePer": row.get("pricePer"),
                "priceSource": "api",
                "actionHint": row.get("actionHint"),
                "targetHint": row.get("targetHint"),
                "supportsReactionChoice": row.get("supportsReactionChoice"),
                "serviceBucket": row.get("serviceName"),
                "platformLabel": "Facebook" if row.get("targetHint") == "fb" else row.get("targetHint"),
            },
            index,
        )
        if not normalized:
            continue
        key = quick_text(normalized["endpoint"] + "|" + normalized["packageName"])
        if key in seen:
            continue
        seen.add(key)
        out.append(enrich_viplike_package_option(normalized))

    out.sort(
        key=lambda item: (
            CANONICAL_FACEBOOK_LIKE_PACKAGE_ORDER.get(normalize_viplike_package_name(item.get("packageName")), 999),
            clean_text(item.get("label")),
        )
    )
    return out


def normalize_viplike_package_option(item: Mapping[str, Any], fallback_index: int = 1) -> dict[str, Any] | None:
    action = quick_text(item.get("action") or item.get("actionHint") or "like")
    platform = quick_text(item.get("platformKey") or item.get("target") or item.get("platform") or item.get("targetHint") or "fb")
    if action and action != "like":
        return None
    if platform and platform not in {"fb", "facebook"}:
        return None

    package_name = normalize_viplike_package_name(item.get("packageName") or item.get("package_name") or item.get("package"))
    endpoint = clean_text(item.get("endpoint") or item.get("urlApi") or item.get("path") or DEFAULT_FACEBOOK_LIKE_ENDPOINT)
    label = clean_text(item.get("label") or item.get("displayName") or item.get("packageTitle") or item.get("name") or package_name or f"Goi like #{fallback_index}")
    haystack = quick_text(" ".join([label, package_name, endpoint, clean_text(item.get("serviceBucket")), clean_text(item.get("platformLabel"))]))
    if not package_name or not endpoint:
        return None
    if any(blocked in haystack for blocked in ("follow", "comment", "cmt", "page", "group")):
        return None
    if not any(marker in haystack for marker in ("like", "reaction", "tim", "tym", "cam xuc", "cảm xúc")):
        return None

    min_qty = max(0, int_number(item.get("minQty") or item.get("min") or item.get("minQuantity") or 50))
    max_qty = max(0, int_number(item.get("maxQty") or item.get("max") or item.get("maxQuantity") or 0))
    package_key = quick_text(package_name).replace("_", " ")
    supports_reaction = (
        bool(item.get("supportsReactionChoice"))
        or bool(item.get("supportsMultiReactionChoice"))
        or package_key in REACTION_PACKAGE_KEYS
        or "reaction" in haystack
        or "cam xuc" in haystack
        or "cảm xúc" in haystack
    )
    allowed = item.get("allowedReactionTypes") or item.get("objectTypes") or item.get("objectType")
    if isinstance(allowed, str):
        allowed = re.split(r"[|,;\s]+", allowed)
    if not isinstance(allowed, list) or not allowed:
        allowed = list(VALID_REACTIONS) if supports_reaction else ["like"]

    price_source = clean_text(item.get("priceSource") or item.get("price_source"))
    base_price = normalize_price(item.get("basePricePer") if "basePricePer" in item else item.get("price_per"))
    price = normalize_price(item.get("pricePer") if "pricePer" in item else item.get("price_per"))
    if not base_price:
        base_price = price
    if not price_source:
        price_source = "api" if base_price or price else "fallback_no_api_price"

    return {
        "id": clean_text(item.get("id") or item.get("packageId") or package_name or f"pkg_{fallback_index}"),
        "label": label,
        "packageName": package_name,
        "endpoint": endpoint,
        "minQty": min_qty,
        "maxQty": max_qty,
        "pricePer": price,
        "basePricePer": base_price,
        "priceSource": price_source,
        "statusType": clean_text(item.get("statusType") or "facebook"),
        "supportsReactionChoice": supports_reaction,
        "supportsMultiReactionChoice": bool(item.get("supportsMultiReactionChoice")),
        "allowedReactionTypes": normalize_reaction_types(allowed),
    }


def enrich_viplike_package_option(option: Mapping[str, Any]) -> dict[str, Any]:
    enriched = dict(option)
    enriched["packageName"] = normalize_viplike_package_name(enriched.get("packageName"))
    package_key = quick_text(enriched.get("packageName") or enriched.get("label")).replace("_", " ")
    allowed = normalize_reaction_types(enriched.get("allowedReactionTypes") or ["like"])
    reaction_capable = bool(enriched.get("supportsReactionChoice")) or package_key in REACTION_PACKAGE_KEYS or len(allowed) > 1
    if reaction_capable:
        enriched["supportsReactionChoice"] = True
        enriched["allowedReactionTypes"] = allowed if len(allowed) > 1 else list(VALID_REACTIONS)
    else:
        enriched["supportsReactionChoice"] = False
        enriched["allowedReactionTypes"] = ["like"]
    enriched["supportsMultiReactionChoice"] = bool(reaction_capable)
    enriched["pricePer"] = normalize_price(enriched.get("pricePer"))
    enriched["basePricePer"] = normalize_price(enriched.get("basePricePer") or enriched.get("pricePer"))
    if not enriched.get("priceSource"):
        enriched["priceSource"] = "catalog" if enriched["basePricePer"] or enriched["pricePer"] else "fallback_no_api_price"
    return enriched


def fallback_viplike_packages() -> list[dict[str, Any]]:
    items = [
        {"id": 1, "label": "S1 Like bam tay", "packageName": "facebook_like", "min": 10, "max": 100000, "pricePer": 28, "reaction": False},
        {"id": 2, "label": "S2 Like clone nhanh", "packageName": "facebook_like_v3", "min": 50, "max": 100000, "pricePer": 10, "reaction": True},
        {"id": 3, "label": "S2 Like sieu nhanh", "packageName": "facebook_like_v7", "min": 50, "max": 100000, "pricePer": 20, "reaction": True},
        {"id": 4, "label": "Like post vip sale", "packageName": "facebook_like_v8", "min": 50, "max": 100000, "pricePer": 9, "reaction": False},
        {"id": 5, "label": "S3 clone TAY sieu re", "packageName": "facebook_like_v10", "min": 10, "max": 100000, "pricePer": 4.09, "reaction": False},
        {"id": 6, "label": "S2 Like post du phong", "packageName": "facebook_like_v33", "min": 50, "max": 100000, "pricePer": 20, "reaction": True},
        {"id": 7, "label": "Like Clone + Vip Reaction", "packageName": "facebook_like_v34", "min": 30, "max": 100000, "pricePer": 10, "reaction": True},
        {"id": 8, "label": "S1 Like clone xin", "packageName": "facebook_like_v401", "min": 50, "max": 100000, "pricePer": 30, "reaction": True},
        {"id": 9, "label": "S3 Like clone xin", "packageName": "facebook_like_v402", "min": 50, "max": 100000, "pricePer": 50, "reaction": True},
    ]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        normalized = normalize_viplike_package_option(
            {
                "id": f"fb_like_api_snapshot_{item['id']}",
                "label": item["label"],
                "packageName": item["packageName"],
                "endpoint": DEFAULT_FACEBOOK_LIKE_ENDPOINT,
                "min": item["min"],
                "max": item["max"],
                "pricePer": item["pricePer"],
                "basePricePer": item["pricePer"],
                "priceSource": "api_snapshot_2026_05_13",
                "supportsReactionChoice": item["reaction"],
                "supportsMultiReactionChoice": item["reaction"],
            },
            index,
        )
        if normalized:
            out.append(enrich_viplike_package_option(normalized))
    return out


def row_supports_reaction_choice(row: Mapping[str, Any]) -> bool:
    if clean_text(row.get("targetHint")) != "fb":
        return False
    package_key = quick_text(row.get("packageName")).replace("_", " ")
    if package_key in REACTION_PACKAGE_KEYS:
        return True
    text = quick_text(" ".join(clean_text(row.get(key)) for key in ("serviceName", "path", "urlApi", "packageName", "packageTitle")))
    return "reaction" in text or "cam xuc" in text or "cảm xúc" in text or "emoji" in text


def detect_smm_action(value: str) -> str:
    text = quick_text(value)
    if "follow" in text:
        return "follow"
    if "comment" in text or "cmt" in text:
        return "comment"
    if "share" in text:
        return "share"
    if any(marker in text for marker in ("like", "reaction", "tim", "tym")):
        return "like"
    return ""


def detect_smm_target(value: str) -> str:
    text = quick_text(value)
    if "facebook" in text or "/facebook-" in text or text.startswith("fb"):
        return "fb"
    if "instagram" in text or "/instagram-" in text:
        return "ig"
    if "tiktok" in text or "/tiktok-" in text:
        return "tt"
    return ""


def normalize_viplike_package_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "_" in raw and " " not in raw:
        return raw
    quick = quick_text(raw)
    quick = re.sub(r"\s+", " ", quick).strip()
    version_match = re.match(r"^facebook like v(\d+)$", quick)
    if version_match:
        return f"facebook_like_v{version_match.group(1)}"
    if quick in {"facebook like", "facebook_like", "goi tang like facebook", "gói tăng like facebook"}:
        return "facebook_like"
    if "facebook" in quick and "like" in quick:
        return re.sub(r"[^a-z0-9]+", "_", quick).strip("_")
    return raw


def normalize_reaction_type(value: Any) -> str:
    text = quick_text(value)
    aliases = {
        "thuong": "care",
        "thương": "care",
        "care": "care",
        "haha": "haha",
        "ha-ha": "haha",
        "phanno": "angry",
        "phan no": "angry",
        "phẫn nộ": "angry",
    }
    text = aliases.get(text, text)
    return text if text in VALID_REACTIONS else "like"


def normalize_reaction_types(values: Any) -> list[str]:
    if isinstance(values, str):
        raw_items = re.split(r"[|,;\s]+", values)
    elif isinstance(values, list):
        raw_items = values
    else:
        raw_items = [values]
    out: list[str] = []
    for item in raw_items:
        reaction = normalize_reaction_type(item)
        if reaction not in out:
            out.append(reaction)
    return out or ["like"]


def build_viplike_order_dedupe_key(claim: Mapping[str, Any]) -> str:
    uid = sanitize_dedupe_part(claim.get("uid"))
    post_id = sanitize_dedupe_part(claim.get("postId") or claim.get("objectId"))
    package_name = sanitize_dedupe_part(normalize_viplike_package_name(claim.get("packageName")))
    quantity = str(max(0, int_number(claim.get("quantity"))))
    reactions = ",".join(sorted(normalize_reaction_types(claim.get("reactionTypes") or claim.get("reactions"))))
    if not uid or not post_id or not package_name or quantity == "0":
        return ""
    return "|".join(["vip1", uid, post_id, package_name, quantity, reactions])


def extract_smm_order_id(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    data = value.get("data") if isinstance(value.get("data"), Mapping) else {}
    order = data.get("order") if isinstance(data.get("order"), Mapping) else {}
    for source in (value, data, order):
        for key in ("order_id", "orderId", "id"):
            found = clean_text(source.get(key))
            if found:
                return found
    return ""


def pick_smm_api_message(value: Any, fallback: str = "") -> str:
    if isinstance(value, Mapping):
        data = value.get("data") if isinstance(value.get("data"), Mapping) else {}
        for source in (value, data):
            for key in ("message", "msg", "error"):
                found = clean_text(source.get(key))
                if found:
                    return found
    return clean_text(fallback)


def parse_json_maybe(body: str) -> dict[str, Any] | list[Any] | None:
    if not body:
        return None
    try:
        return requests.models.complexjson.loads(body)
    except ValueError:
        return None


def normalize_price(value: Any) -> float:
    text = clean_text(value)
    if not text:
        return 0.0
    text = re.sub(r"\s+", "", text)
    if re.match(r"^\d+,\d+$", text):
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def int_number(value: Any) -> int:
    try:
        return int(float(clean_text(value).replace(",", "")))
    except ValueError:
        return 0


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def quick_text(value: Any) -> str:
    return clean_text(value).lower()


def sanitize_dedupe_part(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).lower()).strip()


def sha256_short(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
