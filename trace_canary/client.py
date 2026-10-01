"""Minimal stdio MCP client that drives the fixture through a planted session."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from trace_canary.canaries import canary_by_id

MessageHook = Callable[[str, dict[str, Any]], None]


@dataclass
class StdioMCPClient:
    """JSON-RPC line protocol over a subprocess stdin/stdout."""

    proc: subprocess.Popen[str]
    _next_id: int = 1
    messages: list[dict[str, Any]] = field(default_factory=list)
    on_message: MessageHook | None = None

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        line = json.dumps(payload, separators=(",", ":"))
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        record = {"direction": "client->server", "message": payload}
        self.messages.append(record)
        if self.on_message:
            self.on_message("client->server", payload)

    def _recv(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("MCP server closed stdout unexpectedly")
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            record = {"direction": "server->client", "message": payload}
            self.messages.append(record)
            if self.on_message:
                self.on_message("server->client", payload)
            return payload

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        resp = self._recv()
        if resp.get("id") != req_id:
            raise RuntimeError(f"id mismatch: sent {req_id}, got {resp.get('id')}")
        return resp

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def start_fixture_server(
    python: str | None = None,
    on_message: MessageHook | None = None,
) -> StdioMCPClient:
    py = python or sys.executable
    server = Path(__file__).resolve().parent / "fixture_server.py"
    proc = subprocess.Popen(
        [py, str(server)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return StdioMCPClient(proc=proc, on_message=on_message)


def planted_session_calls() -> list[tuple[str, dict[str, Any]]]:
    """Deterministic tools/call sequence covering all canary vectors."""
    return [
        (
            "store_credential",
            {
                "api_key": canary_by_id("api_key"),
                "bearer": canary_by_id("bearer"),
                "password": canary_by_id("password"),
            },
        ),
        (
            "echo_secret",
            {"secret": canary_by_id("api_key"), "label": "api_key"},
        ),
        (
            "echo_secret",
            {"secret": canary_by_id("jwt"), "label": "jwt"},
        ),
        ("lookup_profile", {"user_id": "canary-user-1"}),
        (
            "connect_db",
            {
                "connection_string": canary_by_id("connstr"),
                "aws_access_key_id": canary_by_id("aws_key"),
            },
        ),
        (
            "register_webhook",
            {
                "url": canary_by_id("webhook"),
                "cookie": canary_by_id("cookie"),
                "jwt": canary_by_id("jwt"),
            },
        ),
        ("fail_with_secret", {"reason": "force-error"}),
    ]


def run_planted_session(client: StdioMCPClient) -> dict[str, Any]:
    """Drive initialize → tools/list → planted tools/call sequence."""
    init = client.request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "trace-canary", "version": "0.1.0"},
        },
    )
    client.notify("notifications/initialized")
    listed = client.request("tools/list")
    tool_results: list[dict[str, Any]] = []
    for name, args in planted_session_calls():
        resp = client.request("tools/call", {"name": name, "arguments": args})
        tool_results.append({"tool": name, "response": resp})
    return {
        "initialize": init,
        "tools_list": listed,
        "tool_results": tool_results,
        "message_count": len(client.messages),
    }
