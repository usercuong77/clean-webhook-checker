# Instagram Checker Service

Small isolated Playwright service for Instagram live/die checks.

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
  -d "{\"username\":\"tomlulofs\"}"
```

Bulk:

```powershell
curl -X POST http://127.0.0.1:8091/ig/check-bulk `
  -H "Content-Type: application/json" `
  -d "{\"items\":[{\"username\":\"tomlulofs\"},{\"username\":\"dsgfsdgsg\"}]}"
```

## Render

Use `render.yaml` with Docker. The service runs from the official Playwright Python image, so Chromium and OS deps are already included.
