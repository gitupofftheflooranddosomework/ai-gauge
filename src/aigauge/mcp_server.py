from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config, display_name_for_account
from .usage_cache import read_usage_cache

INSTRUCTIONS = """
AI Gauge provides local subscription usage and user-configured pause policies.
Before starting costly AI work, call check_current_account_usage. If allowed is
false, stop and tell the user why. This is a cooperative guard: the MCP protocol
cannot suspend a client that ignores the result.
""".strip()

mcp = FastMCP("AI Gauge", instructions=INSTRUCTIONS, json_response=True)
_bound_account_id: str | None = None
_auto_codex_account = False


def _codex_auth_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home) / "auth.json" if codex_home else Path.home() / ".codex" / "auth.json"


def _jwt_payload(token: str) -> dict[str, Any]:
    """Decode local JWT claims without logging or returning the credential."""
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError, UnicodeError, json.JSONDecodeError):
        return {}


def _active_codex_identity() -> dict[str, str] | None:
    """Read the currently logged-in Codex identity from its local auth metadata."""
    try:
        auth = json.loads(_codex_auth_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    if not isinstance(tokens, dict):
        return None
    claims = _jwt_payload(str(tokens.get("id_token") or ""))
    provider_auth = claims.get("https://api.openai.com/auth")
    if not isinstance(provider_auth, dict):
        provider_auth = {}
    email = claims.get("email")
    account_id = provider_auth.get("chatgpt_account_id") or tokens.get("account_id")
    identity = {}
    if isinstance(email, str) and email.strip():
        identity["email"] = email.strip().lower()
    if isinstance(account_id, str) and account_id.strip():
        identity["chatgpt_account_id"] = account_id.strip()
    return identity or None


def _identity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _resolve_active_codex_account(config: Config) -> tuple[str | None, str]:
    identity = _active_codex_identity()
    if identity is None:
        return None, "Codex is logged out or its active identity could not be read."
    email = identity.get("email", "")
    local_part = email.partition("@")[0]
    wanted = _identity_key(local_part)
    matches = [
        account.id
        for account in config.browser_accounts
        if account.kind == "codex"
        and account.name
        and _identity_key(account.name) == wanted
    ]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return (
            None,
            f"No AI Gauge Codex account is named for the active login ({local_part}).",
        )
    return None, f"More than one AI Gauge Codex account matches the active login ({local_part})."


def _account_rows() -> list[dict[str, Any]]:
    config = Config.load()
    cache = read_usage_cache()
    cached = cache.get("accounts", {})
    account_ids = list(cached) if isinstance(cached, dict) else []
    for account_id in config.mcp_pause_policies:
        if account_id not in account_ids:
            account_ids.append(account_id)
    rows = []
    for account_id in account_ids:
        snapshot = cached.get(account_id, {}) if isinstance(cached, dict) else {}
        metrics = snapshot.get("metrics", []) if isinstance(snapshot, dict) else []
        percentages = [
            metric.get("percent_used")
            for metric in metrics
            if isinstance(metric, dict) and metric.get("percent_used") is not None
        ]
        try:
            display_name = display_name_for_account(config, account_id)
        except Exception:
            display_name = account_id
        rows.append(
            {
                "account_id": account_id,
                "display_name": display_name,
                "status": snapshot.get("status", "unknown"),
                "fetched_at": snapshot.get("fetched_at"),
                "max_percent_used": max(percentages) if percentages else None,
                "metrics": metrics,
                "pause_at_percent": config.mcp_pause_policies.get(account_id),
            }
        )
    return rows


@mcp.tool()
def get_ai_usage(account_id: str | None = None) -> dict[str, Any]:
    """Return sanitized AI Gauge usage for all accounts or one account."""
    rows = _account_rows()
    if account_id is not None:
        rows = [row for row in rows if row["account_id"] == account_id]
    return {"accounts": rows}


@mcp.tool()
def check_usage_guard(account_id: str) -> dict[str, Any]:
    """Check whether configured usage policy allows more work on an account."""
    row = next(
        (item for item in _account_rows() if item["account_id"] == account_id),
        None,
    )
    if row is None:
        return {
            "allowed": False,
            "account_id": account_id,
            "reason": "No usage data is available for this account.",
        }
    threshold = row["pause_at_percent"]
    percent = row["max_percent_used"]
    if threshold is None:
        return {
            "allowed": True,
            "account": row,
            "reason": "No MCP pause policy is configured for this account.",
        }
    if percent is None:
        return {
            "allowed": False,
            "account": row,
            "reason": "Usage is unknown; the configured guard fails closed.",
        }
    allowed = percent < threshold
    return {
        "allowed": allowed,
        "account": row,
        "reason": (
            f"Usage {percent:g}% is below the {threshold}% pause threshold."
            if allowed
            else f"Usage {percent:g}% reached the {threshold}% pause threshold."
        ),
    }


@mcp.tool()
def check_current_account_usage() -> dict[str, Any]:
    """Check the explicitly bound account or the currently logged-in Codex account."""
    if _auto_codex_account:
        account_id, reason = _resolve_active_codex_account(Config.load())
        if account_id is None:
            return {"allowed": False, "account_id": None, "reason": reason}
        result = check_usage_guard(account_id)
        result["resolved_from_active_codex_login"] = True
        return result
    if _bound_account_id is None:
        return {
            "allowed": False,
            "account_id": None,
            "reason": (
                "This MCP connection is not bound to an AI Gauge account. "
                "Launch it with --account-id <account-id>."
            ),
        }
    return check_usage_guard(_bound_account_id)


@mcp.tool()
def recommend_ai_account() -> dict[str, Any]:
    """Recommend the account with the most policy headroom."""
    candidates = []
    for row in _account_rows():
        percent = row["max_percent_used"]
        threshold = row["pause_at_percent"]
        if percent is None:
            continue
        limit = threshold if threshold is not None else 100
        candidates.append((limit - percent, row))
    if not candidates:
        return {"account": None, "reason": "No current usage data is available."}
    headroom, row = max(candidates, key=lambda item: item[0])
    return {"account": row, "headroom_percent": headroom}


@mcp.resource("aigauge://usage")
def usage_resource() -> str:
    """Current sanitized usage snapshot as JSON."""
    return json.dumps({"accounts": _account_rows()}, indent=2)


@mcp.resource("aigauge://guard-instructions")
def guard_instructions_resource() -> str:
    """Instructions clients should follow to honor pause policies."""
    return INSTRUCTIONS


def main() -> None:
    global _auto_codex_account, _bound_account_id
    parser = argparse.ArgumentParser(description="AI Gauge MCP usage server")
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument(
        "--account-id",
        help="AI Gauge account used by this MCP client/profile.",
    )
    binding.add_argument(
        "--auto-codex-account",
        action="store_true",
        help="Resolve the AI Gauge account from the currently logged-in Codex identity.",
    )
    args = parser.parse_args()
    _bound_account_id = args.account_id
    _auto_codex_account = args.auto_codex_account
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
