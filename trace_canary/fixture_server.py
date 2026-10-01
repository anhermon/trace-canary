#!/usr/bin/env python3
"""Fixture MCP server (stdio) that plants canary secrets in args/results/errors.

Speaks just enough MCP JSON-RPC for mcp-trace / a local client:
initialize, tools/list, tools/call, ping. No third-party deps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow `python fixture_server.py` from any cwd.
_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trace_canary.canaries import canary_by_id  # noqa: E402

SERVER_NAME = "trace-canary-fixture"
SERVER_VERSION = "0.1.0"


def _c(cid: str) -> str:
    return canary_by_id(cid)


TOOLS = [
    {
        "name": "store_credential",
        "description": "Accepts API key / bearer / password canaries as tool arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "bearer": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["api_key"],
        },
    },
    {
        "name": "echo_secret",
        "description": "Echoes a secret back in the tool result (arg + result leak vectors).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "secret": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": ["secret"],
        },
    },
    {
        "name": "lookup_profile",
        "description": "Returns a fake profile containing PII canaries in the result body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "connect_db",
        "description": "Accepts a connection-string canary; returns AWS key + private-key marker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection_string": {"type": "string"},
                "aws_access_key_id": {"type": "string"},
            },
            "required": ["connection_string"],
        },
    },
    {
        "name": "register_webhook",
        "description": "Accepts webhook URL + session cookie canaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "cookie": {"type": "string"},
                "jwt": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fail_with_secret",
        "description": "Always fails; embeds error_detail canary in tool error and RPC message.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
        },
    },
]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ok_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "store_credential":
        api_key = str(arguments.get("api_key", ""))
        bearer = str(arguments.get("bearer", ""))
        password = str(arguments.get("password", ""))
        # Reflect keys (not full values) so a well-behaved exporter can omit values
        # while a naive one that dumps arguments still leaks.
        return _ok_text(
            f"stored credential fingerprint api_key_len={len(api_key)} "
            f"bearer_len={len(bearer)} password_len={len(password)}"
        )

    if name == "echo_secret":
        secret = str(arguments.get("secret", ""))
        label = str(arguments.get("label", "secret"))
        # Intentional result leak vector.
        return _ok_text(f"echo[{label}]={secret}")

    if name == "lookup_profile":
        uid = str(arguments.get("user_id", "unknown"))
        profile = {
            "user_id": uid,
            "email": _c("email"),
            "ssn": _c("ssn"),
            "notes": "fixture profile — canaries planted on purpose",
        }
        return _ok_text(json.dumps(profile))

    if name == "connect_db":
        # Result also plants aws_key + privkey marker.
        return _ok_text(
            json.dumps(
                {
                    "status": "connected",
                    "aws_access_key_id": _c("aws_key"),
                    "tls_material": _c("privkey_marker"),
                }
            )
        )

    if name == "register_webhook":
        url = str(arguments.get("url", ""))
        return _ok_text(f"registered webhook host from url_len={len(url)}")

    if name == "fail_with_secret":
        detail = _c("error_detail")
        return _err_text(f"upstream failure: {detail}")

    return _err_text(f"unknown tool: {name}")


def handle(req: dict[str, Any]) -> dict[str, Any] | None:
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    if req_id is None:
        _log(f"notification: {method}")
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if name == "fail_with_secret":
            # Also surface as JSON-RPC error so exporters that only log rpc errors
            # still have a canary to leak (or redact).
            detail = _c("error_detail")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": f"tool failed: {detail}",
                    "data": {"tool": name, "detail": detail},
                },
            }
        result = call_tool(name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    _log(f"{SERVER_NAME} {SERVER_VERSION} ready (stdio)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"bad json: {exc}")
            continue
        resp = handle(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
