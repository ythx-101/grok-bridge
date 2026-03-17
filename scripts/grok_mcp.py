#!/usr/bin/env python3
"""
grok_mcp.py — MCP server for Grok Bridge

Wraps the running grok_bridge.py HTTP API as MCP tools.
Requires grok_bridge.py running on localhost:19998.

Usage (Claude Code settings.json):
  "mcpServers": {
    "grok": {
      "command": "python3",
      "args": ["/path/to/grok_mcp.py"]
    }
  }
"""
import json
import urllib.request
from mcp.server.fastmcp import FastMCP

BRIDGE_URL = "http://localhost:19998"

mcp = FastMCP("grok-bridge")


def _post(path: str, data: dict | None = None) -> dict:
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BRIDGE_URL}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def _send(prompt: str, timeout: int) -> str:
    result = _post("/chat", {"prompt": prompt, "timeout": timeout})
    if result.get("status") == "ok":
        return result.get("response", "")
    elif result.get("status") == "timeout":
        return f"[timeout after {result.get('elapsed', '?')}s] {result.get('response', '')}"
    else:
        return f"[error] {result.get('error', 'unknown error')}"


@mcp.tool()
def grok_chat(prompt: str, timeout: int = 120) -> str:
    """Start a NEW Grok conversation and send a message. Previous conversation is preserved in Grok history.

    Args:
        prompt: The message to send to Grok
        timeout: Max seconds to wait for response (default 120)
    """
    _post("/new")
    return _send(prompt, timeout)


@mcp.tool()
def grok_continue_chat(prompt: str, timeout: int = 120) -> str:
    """Continue the CURRENT Grok conversation. Use this for follow-up questions in the same topic.

    Args:
        prompt: The follow-up message to send to Grok
        timeout: Max seconds to wait for response (default 120)
    """
    return _send(prompt, timeout)


@mcp.tool()
def grok_history() -> str:
    """Read the current Grok conversation history from the page."""
    result = _get("/history")
    if result.get("status") == "ok":
        return result.get("content", "(empty)")
    return f"[error] {result.get('error', '')}"


if __name__ == "__main__":
    mcp.run()
