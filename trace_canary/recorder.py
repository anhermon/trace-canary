"""Record MCP session traffic into JSONL / OTLP-ish JSON / SQLite artifacts."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from trace_canary.canaries import redact_text

Mode = Literal["raw", "redact"]


class ArtifactWriter:
    """Writes session artifacts; optionally redacts known canaries before persist."""

    def __init__(self, out_dir: Path, mode: Mode = "redact") -> None:
        self.out_dir = out_dir
        self.mode = mode
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.out_dir / "session.jsonl"
        self.otlp_path = self.out_dir / "otlp-export.json"
        self.sqlite_path = self.out_dir / "traces.sqlite"
        self._spans: list[dict[str, Any]] = []
        self._jsonl = self.jsonl_path.open("w", encoding="utf-8")
        self._init_sqlite()

    def _maybe_redact(self, obj: Any) -> Any:
        text = json.dumps(obj, ensure_ascii=False)
        if self.mode == "redact":
            text = redact_text(text)
        return json.loads(text)

    def _init_sqlite(self) -> None:
        if self.sqlite_path.exists():
            self.sqlite_path.unlink()
        con = sqlite3.connect(self.sqlite_path)
        try:
            con.execute(
                """
                CREATE TABLE spans (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    direction TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                )
                """
            )
            con.commit()
        finally:
            con.close()

    def write_message(self, direction: str, message: dict[str, Any]) -> None:
        safe = self._maybe_redact(message)
        record = {
            "ts": time.time(),
            "direction": direction,
            "message": safe,
        }
        self._jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._jsonl.flush()

        con = sqlite3.connect(self.sqlite_path)
        try:
            con.execute(
                "INSERT INTO messages(direction, payload_json, recorded_at) VALUES (?,?,?)",
                (direction, json.dumps(safe, ensure_ascii=False), time.time()),
            )
            con.commit()
        finally:
            con.close()

        # Derive a pseudo-span for tools/call so OTLP-ish export has content.
        if direction == "client->server" and safe.get("method") == "tools/call":
            params = safe.get("params") or {}
            self._pending_tool = {
                "name": params.get("name", "unknown"),
                "arguments": params.get("arguments") or {},
                "start": time.time(),
            }
        elif direction == "server->client" and hasattr(self, "_pending_tool"):
            pending = self._pending_tool
            del self._pending_tool
            status = "error" if "error" in safe else "ok"
            attrs = {
                "mcp.method": "tools/call",
                "mcp.tool.name": pending["name"],
                "mcp.tool.argument_keys": ",".join(sorted(pending["arguments"])),
                "mcp.status": status,
            }
            # Simulate a naive exporter that also dumps args/results (raw mode leaks).
            if self.mode == "raw":
                attrs["mcp.tool.arguments"] = pending["arguments"]
                if "result" in safe:
                    attrs["mcp.tool.result"] = safe["result"]
                if "error" in safe:
                    attrs["error.message"] = safe["error"]
            else:
                # Redacting exporter: keys only + redacted error message if any.
                if "error" in safe:
                    attrs["error.message"] = redact_text(
                        json.dumps(safe["error"], ensure_ascii=False)
                    )
            span = {
                "name": f"mcp tools/call {pending['name']}",
                "status": status,
                "attributes": attrs,
                "duration_ms": round((time.time() - pending["start"]) * 1000, 3),
            }
            self._spans.append(span)
            con = sqlite3.connect(self.sqlite_path)
            try:
                con.execute(
                    "INSERT INTO spans(name, status, attributes_json, recorded_at) VALUES (?,?,?,?)",
                    (
                        span["name"],
                        span["status"],
                        json.dumps(span["attributes"], ensure_ascii=False),
                        time.time(),
                    ),
                )
                con.commit()
            finally:
                con.close()

    def finalize(self, meta: dict[str, Any] | None = None) -> dict[str, Path]:
        export = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "trace-canary"}},
                            {
                                "key": "trace.canary.mode",
                                "value": {"stringValue": self.mode},
                            },
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "trace-canary.recorder", "version": "0.1.0"},
                            "spans": [
                                {
                                    "name": s["name"],
                                    "status": {"code": 2 if s["status"] == "error" else 1},
                                    "attributes": [
                                        {
                                            "key": k,
                                            "value": {
                                                "stringValue": v
                                                if isinstance(v, str)
                                                else json.dumps(v, ensure_ascii=False)
                                            },
                                        }
                                        for k, v in s["attributes"].items()
                                    ],
                                }
                                for s in self._spans
                            ],
                        }
                    ],
                }
            ],
            "meta": meta or {},
        }
        # Final pass — keep OTLP file consistent with mode.
        if self.mode == "redact":
            export = json.loads(redact_text(json.dumps(export, ensure_ascii=False)))
        self.otlp_path.write_text(json.dumps(export, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._jsonl.close()
        summary = {
            "mode": self.mode,
            "jsonl": str(self.jsonl_path),
            "otlp": str(self.otlp_path),
            "sqlite": str(self.sqlite_path),
            "span_count": len(self._spans),
        }
        (self.out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "jsonl": self.jsonl_path,
            "otlp": self.otlp_path,
            "sqlite": self.sqlite_path,
            "summary": self.out_dir / "summary.json",
        }
