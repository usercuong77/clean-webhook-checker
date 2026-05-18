from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests


DEFAULT_BASE_URL = "https://clean-webhook-checker.onrender.com"
TIMEOUT_SECONDS = 120

CHECK_CASES = [
    {
        "input": "https://www.facebook.com/kieu.anh.511762",
        "status": "LIVE",
        "uid": "100013996607571",
    },
    {
        "input": "https://www.facebook.com/share/1Ay9R878jq/",
        "status": "LIVE",
        "uid": "100056144974786",
    },
    {
        "input": "http://facebook.com/61574756686411",
        "status": "LIVE",
        "uid": "61574756686411",
    },
    {
        "input": "https://www.facebook.com/kieu.anh.51176299",
        "status": "DIE",
        "uid": "",
    },
    {
        "input": "https://www.facebook.com/nguyen.lien.984608#",
        "status": "LIVE",
        "uid": "100015820237115",
    },
    {
        "input": "https://www.facebook.com/hong.duyen.tran.594446",
        "status": "LIVE",
        "uid": "100004192098772",
    },
]

LATEST_POST_CASES = [
    {
        "input": "https://www.facebook.com/kieu.anh.511762",
        "ok": True,
        "uid": "100013996607571",
        "postId": "2402282520248278",
    },
    {
        "input": "https://www.facebook.com/share/1Ay9R878jq/",
        "ok": True,
        "uid": "100056144974786",
        "postId": "1445441457337340",
    },
    {
        "input": "http://facebook.com/61574756686411",
        "ok": True,
        "uid": "61574756686411",
        "postId": "122172117554825222",
    },
    {
        "input": "https://www.facebook.com/kieu.anh.51176299",
        "ok": False,
        "uid": "",
        "postId": "",
    },
    {
        "input": "https://www.facebook.com/nguyen.lien.984608#",
        "ok": True,
        "uid": "100015820237115",
        "postId": "2128729774331010",
    },
    {
        "input": "https://www.facebook.com/hong.duyen.tran.594446",
        "ok": True,
        "uid": "100004192098772",
        "postId": "797228153760247",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed production smoke checks for clean-webhook-checker.")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--skip-latest-post", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = _headers()
    failures: list[str] = []

    health = requests.get(f"{base_url}/health", headers=headers, timeout=30)
    _print_row("HEALTH", {"http": health.status_code, "body": _json_or_raw(health)})
    if health.status_code != 200:
        failures.append(f"health_http_{health.status_code}")

    for case in CHECK_CASES:
        response = requests.post(
            f"{base_url}/check",
            json={"input": case["input"], "mode": "1", "includeName": True},
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        body = _json_or_raw(response)
        row = {
            "input": case["input"],
            "http": response.status_code,
            "status": body.get("status"),
            "uid": body.get("uid"),
            "name": body.get("name"),
            "source": body.get("source"),
            "reason": body.get("reason"),
            "elapsedMs": body.get("elapsedMs"),
            "resolverDebug": body.get("resolverDebug"),
        }
        _print_row("CHECK", row)
        if response.status_code != 200:
            failures.append(f"check_http:{case['input']}:{response.status_code}")
        if body.get("status") != case["status"] or str(body.get("uid", "")) != case["uid"]:
            failures.append(f"check_mismatch:{case['input']}")

    if not args.skip_latest_post:
        for case in LATEST_POST_CASES:
            response = requests.post(
                f"{base_url}/latest-post",
                json={"input": case["input"], "includeContent": True},
                headers=headers,
                timeout=TIMEOUT_SECONDS,
            )
            body = _json_or_raw(response)
            row = {
                "input": case["input"],
                "http": response.status_code,
                "ok": body.get("ok"),
                "uid": body.get("uid"),
                "postId": body.get("postId"),
                "reason": body.get("reason"),
                "method": body.get("method"),
                "elapsedMs": body.get("elapsedMs"),
                "content": str(body.get("content") or "")[:160],
            }
            _print_row("POST", row)
            if response.status_code != 200:
                failures.append(f"latest_http:{case['input']}:{response.status_code}")
            if bool(body.get("ok")) != bool(case["ok"]):
                failures.append(f"latest_ok_mismatch:{case['input']}")
            if str(body.get("uid", "")) != case["uid"] or str(body.get("postId", "")) != case["postId"]:
                failures.append(f"latest_value_mismatch:{case['input']}")

    if failures:
        _print_row("FAIL", {"failures": failures})
        return 1

    _print_row("PASS", {"checkCases": len(CHECK_CASES), "latestPostCases": 0 if args.skip_latest_post else len(LATEST_POST_CASES)})
    return 0


def _headers() -> dict[str, str]:
    api_key = os.getenv("CHECKER_API_KEY", "").strip()
    return {"X-Api-Key": api_key} if api_key else {}


def _json_or_raw(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}
    except ValueError:
        return {"raw": response.text[:500]}


def _print_row(label: str, payload: dict[str, Any]) -> None:
    print(f"{label} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
