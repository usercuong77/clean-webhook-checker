def status_label(status: str) -> str:
    value = (status or "UNKNOWN").upper()
    if value in {"LIVE", "DIE", "UNKNOWN"}:
        return value
    return "UNKNOWN"
