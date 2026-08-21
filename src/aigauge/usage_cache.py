from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

from .config import app_data_dir
from .models import UsageSnapshot


def usage_cache_path() -> Path:
    return app_data_dir() / "mcp-usage.json"


def write_usage_cache(snapshots: dict[str, UsageSnapshot]) -> None:
    """Publish sanitized usage data; provider credentials are never included."""
    payload: dict[str, object] = {"accounts": {}}
    accounts = payload["accounts"]
    assert isinstance(accounts, dict)
    for account_id, snapshot in snapshots.items():
        accounts[account_id] = {
            "status": snapshot.status.value,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "error": snapshot.error,
            "metrics": [
                {
                    "label": metric.label,
                    "percent_used": metric.percent_used,
                    "resets_at": (
                        metric.resets_at.isoformat() if metric.resets_at else None
                    ),
                    "reset_label": metric.reset_label,
                    "note": metric.note,
                }
                for metric in snapshot.metrics
            ],
        }
    path = usage_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed .tmp name lets overlapping writers clobber one another. On
    # Windows, readers may also briefly hold the destination without delete
    # sharing, making os.replace raise WinError 32. Give every publication its
    # own closed temporary file and retry that short sharing window.
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        for attempt in range(6):
            try:
                os.replace(temporary_path, path)
                temporary_path = None
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_usage_cache() -> dict:
    path = usage_cache_path()
    if not path.exists():
        return {"accounts": {}, "message": "AI Gauge has not published usage yet."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"accounts": {}}
    except (OSError, json.JSONDecodeError):
        return {"accounts": {}, "message": "AI Gauge usage cache is unavailable."}
