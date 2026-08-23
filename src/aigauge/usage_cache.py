from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time

from .config import app_data_dir
from .models import UsageSnapshot


CACHE_SCHEMA_VERSION = 1
_MAX_CACHE_BYTES = 1024 * 1024


def usage_cache_path() -> Path:
    return app_data_dir() / "mcp-usage.json"


def _empty_cache(message: str) -> dict:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "published_at": None,
        "accounts": {},
        "message": message,
    }


def write_usage_cache(snapshots: dict[str, UsageSnapshot]) -> None:
    """Publish the minimal sanitized usage projection used by the MCP guard."""
    payload: dict[str, object] = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "published_at": datetime.now().isoformat(),
        "accounts": {},
    }
    accounts = payload["accounts"]
    assert isinstance(accounts, dict)
    for account_id, snapshot in snapshots.items():
        accounts[account_id] = {
            "status": snapshot.status.value,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "metrics": [
                {
                    "label": metric.label,
                    "percent_used": metric.percent_used,
                    "resets_at": (
                        metric.resets_at.isoformat() if metric.resets_at else None
                    ),
                    "reset_label": metric.reset_label,
                    # Tagged rows are presentation breakdowns, not limit gauges.
                    "guard_eligible": metric.tag is None,
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
            json.dump(payload, temporary, indent=2, allow_nan=False)
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


def invalidate_usage_cache() -> None:
    path = usage_cache_path()
    for attempt in range(6):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def read_usage_cache() -> dict:
    path = usage_cache_path()
    if not path.exists():
        return _empty_cache("AI Gauge has not published MCP usage.")
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return _empty_cache("AI Gauge MCP usage cache is too large.")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _empty_cache(
            "AI Gauge MCP usage cache is invalid."
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return _empty_cache("AI Gauge MCP usage cache is unavailable.")
