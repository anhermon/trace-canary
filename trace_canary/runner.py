"""Orchestrate fixture session → artifact recording → optional mcp-trace note."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from trace_canary.client import run_planted_session, start_fixture_server
from trace_canary.recorder import ArtifactWriter, Mode


def _mcp_trace_info() -> dict[str, Any]:
    path = shutil.which("mcp-trace")
    info: dict[str, Any] = {"on_path": bool(path), "path": path}
    if not path:
        info["hint"] = (
            "Install from https://github.com/anhermon/mcp-trace/releases then re-run "
            "with TRACE_CANARY_WRAP_MCP_TRACE=1 to dogfood the proxy (capture_tool_args=false)."
        )
        return info
    try:
        proc = subprocess.run(
            [path, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        info["version_stdout"] = (proc.stdout or proc.stderr or "").strip()
    except OSError as exc:
        info["version_error"] = str(exc)
    return info


def run_harness(out_dir: str | Path, mode: Mode = "redact") -> dict[str, Any]:
    """Run planted MCP session and write artifacts under out_dir."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    writer = ArtifactWriter(out, mode=mode)

    def on_message(direction: str, message: dict[str, Any]) -> None:
        writer.write_message(direction, message)

    client = start_fixture_server(on_message=on_message)
    stderr = ""
    try:
        session = run_planted_session(client)
    finally:
        client.close()
        if client.proc.stderr:
            try:
                stderr = client.proc.stderr.read() or ""
            except OSError:
                stderr = ""

    paths = writer.finalize(
        meta={
            "mode": mode,
            "message_count": session.get("message_count"),
            "tool_calls": len(session.get("tool_results") or []),
            "fixture_stderr_tail": stderr[-2000:],
            "mcp_trace": _mcp_trace_info(),
            "wrap_env": os.environ.get("TRACE_CANARY_WRAP_MCP_TRACE"),
        }
    )
    return {
        "mode": mode,
        "out_dir": str(out),
        "artifacts": {k: str(v) for k, v in paths.items()},
        "session": {
            "message_count": session.get("message_count"),
            "tool_calls": len(session.get("tool_results") or []),
        },
        "mcp_trace": _mcp_trace_info(),
    }


def write_run_report(result: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
