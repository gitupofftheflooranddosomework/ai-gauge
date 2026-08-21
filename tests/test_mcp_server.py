from datetime import datetime

from aigauge.config import BrowserAccount, Config
from aigauge import mcp_server
from aigauge.mcp_server import check_usage_guard, recommend_ai_account
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.usage_cache import read_usage_cache, write_usage_cache


def _cache(percent: float) -> dict:
    return {
        "accounts": {
            "codex": {
                "status": "ok",
                "fetched_at": datetime.now().isoformat(),
                "metrics": [{"label": "Weekly", "percent_used": percent}],
            }
        }
    }


def test_guard_blocks_at_configured_threshold(monkeypatch):
    config = Config()
    config.mcp_pause_policies = {"codex": 90}
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: _cache(91))

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert "reached" in result["reason"]


def test_guard_allows_below_threshold(monkeypatch):
    config = Config()
    config.mcp_pause_policies = {"codex": 90}
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: _cache(72))

    assert check_usage_guard("codex")["allowed"] is True


def test_current_account_guard_uses_explicit_binding(monkeypatch):
    config = Config()
    config.mcp_pause_policies = {"codex-work": 80}
    cache = {
        "accounts": {
            "codex-work": {
                "status": "ok",
                "metrics": [{"label": "Weekly", "percent_used": 85}],
            },
            "codex-personal": {
                "status": "ok",
                "metrics": [{"label": "Weekly", "percent_used": 10}],
            },
        }
    }
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: cache)
    monkeypatch.setattr(mcp_server, "_bound_account_id", "codex-work")
    monkeypatch.setattr(mcp_server, "_auto_codex_account", False)

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is False
    assert result["account"]["account_id"] == "codex-work"


def test_current_account_guard_tracks_active_codex_login(monkeypatch):
    config = Config()
    config.browser_accounts = [
        BrowserAccount(id="codex-mshaw", kind="codex", name="mshaw"),
        BrowserAccount(id="codex-mhws97", kind="codex", name="mhws97"),
    ]
    config.mcp_pause_policies = {"codex-mshaw": 90}
    cache = {
        "accounts": {
            "codex-mshaw": {
                "status": "ok",
                "metrics": [{"label": "Weekly", "percent_used": 7}],
            }
        }
    }
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: cache)
    monkeypatch.setattr(
        mcp_server,
        "_active_codex_identity",
        lambda: {"email": "mshaw@prowrench.ca"},
    )
    monkeypatch.setattr(mcp_server, "_auto_codex_account", True)

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is True
    assert result["account"]["account_id"] == "codex-mshaw"
    assert result["resolved_from_active_codex_login"] is True


def test_current_account_guard_fails_closed_when_codex_logged_out(monkeypatch):
    monkeypatch.setattr(mcp_server, "_active_codex_identity", lambda: None)
    monkeypatch.setattr(mcp_server, "_auto_codex_account", True)

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is False
    assert "logged out" in result["reason"]


def test_current_account_guard_fails_closed_for_unmapped_login(monkeypatch):
    config = Config()
    config.browser_accounts = [
        BrowserAccount(id="codex-mhws97", kind="codex", name="mhws97")
    ]
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr(
        mcp_server,
        "_active_codex_identity",
        lambda: {"email": "someone-else@example.com"},
    )
    monkeypatch.setattr(mcp_server, "_auto_codex_account", True)

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is False
    assert "No AI Gauge Codex account" in result["reason"]


def test_recommendation_uses_most_policy_headroom(monkeypatch):
    config = Config()
    config.mcp_pause_policies = {"codex": 90, "claude": 80}
    cache = _cache(70)
    cache["accounts"]["claude"] = {
        "status": "ok",
        "metrics": [{"label": "Weekly", "percent_used": 20}],
    }
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: cache)

    result = recommend_ai_account()

    assert result["account"]["account_id"] == "claude"
    assert result["headroom_percent"] == 60


def test_usage_cache_omits_raw_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Weekly", 42)],
        raw={"cookie": "must-not-leak"},
    )

    write_usage_cache({"codex": snapshot})
    cached = read_usage_cache()

    assert cached["accounts"]["codex"]["metrics"][0]["percent_used"] == 42
    assert "must-not-leak" not in str(cached)


def test_usage_cache_retries_windows_sharing_violation(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Weekly", 42)],
    )
    real_replace = mcp_server.os.replace
    attempts = 0

    def briefly_locked(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(32, "file is being used by another process")
        real_replace(source, destination)

    monkeypatch.setattr("aigauge.usage_cache.os.replace", briefly_locked)
    monkeypatch.setattr("aigauge.usage_cache.time.sleep", lambda _delay: None)

    write_usage_cache({"codex": snapshot})

    assert attempts == 3
    assert read_usage_cache()["accounts"]["codex"]["status"] == "ok"
    assert list(tmp_path.glob("*.tmp")) == []
