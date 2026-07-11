# Instagram Checker Service

Isolated Playwright service for Instagram LIVE/DIE checks. It does not run
inside the Facebook Render pool. Cloudflare only schedules work and stores
confirmed state changes.

The checker uses two layers:

1. A profile API request executed inside a real Chromium page.
2. A lightweight profile-page fallback when Instagram throttles that API.

HTTP 401, 403, 429, timeout, and backend errors never become `DIE` unless the
profile page exposes a strong unavailable signal.

## Local

```powershell
pip install -r requirements.txt
python -m playwright install chromium
uvicorn main:app --host 127.0.0.1 --port 8091
```

## API

```powershell
curl -X POST http://127.0.0.1:8091/ig/check `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $env:IG_CHECKER_API_KEY" `
  -d "{\"username\":\"tomlulofs\"}"
```

Bulk:

```powershell
curl -X POST http://127.0.0.1:8091/ig/check-bulk `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $env:IG_CHECKER_API_KEY" `
  -d "{\"items\":[{\"username\":\"tomlulofs\"},{\"username\":\"dsgfsdgsg\"}]}"
```

## Render

Use the Docker runtime from `render.yaml`. Required production settings:

- `IG_CHECKER_API_KEY`: shared secret used by Cloudflare.
- `IG_CHECK_CONCURRENCY=1`: one browser request per session.
- `IG_MIN_REQUEST_INTERVAL_MS=2200`: protects the Instagram session/IP.
- `IG_PRIMARY_WITH_COOKIE=0`: anonymous browser profile lookup is the primary
  path. Cookie input remains optional and must not be committed.

To add capacity later, deploy the same service to another Render/VPS and add
its URL to `IG_CHECKER_URLS` in Cloudflare. The client distributes usernames
round-robin and preserves input order.
