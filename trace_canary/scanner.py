"""Recursive scanner: fail if any canary appears in trace artifacts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from trace_canary.canaries import load_canaries

# Text-ish suffixes we always decode as UTF-8 (lossy).
TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".txt",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".csv",
    ".ndjson",
    ".xml",
    ".html",
    ".js",
    ".ts",
    ".py",
}

BINARY_DB_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".db3"}

SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}


@dataclass
class Leak:
    canary_id: str
    value_preview: str
    path: str
    offset: int | None = None
    context: str = ""


@dataclass
class ScanResult:
    scanned_files: int = 0
    leaks: list[Leak] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.leaks


def _preview(value: str, n: int = 48) -> str:
    return value if len(value) <= n else value[: n - 3] + "..."


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _scan_bytes(data: bytes, path: Path, canaries: list[dict]) -> list[Leak]:
    leaks: list[Leak] = []
    # Prefer UTF-8 text view; fall back to latin-1 so binary still searchable.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    for c in canaries:
        val = str(c["value"])
        start = 0
        while True:
            idx = text.find(val, start)
            if idx < 0:
                break
            lo = max(0, idx - 40)
            hi = min(len(text), idx + len(val) + 40)
            ctx = text[lo:hi].replace("\n", "\\n")
            leaks.append(
                Leak(
                    canary_id=str(c["id"]),
                    value_preview=_preview(val),
                    path=str(path),
                    offset=idx,
                    context=ctx,
                )
            )
            start = idx + len(val)
    return leaks


def _scan_sqlite(path: Path, canaries: list[dict]) -> list[Leak]:
    leaks: list[Leak] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        # Unreadable as SQLite — fall back to raw bytes.
        return _scan_bytes(path.read_bytes(), path, canaries)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            if not cols:
                continue
            # Quote identifiers lightly; table names come from sqlite_master.
            col_list = ", ".join(f'"{c}"' for c in cols)
            try:
                rows = con.execute(f'SELECT {col_list} FROM "{table}"').fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                blob = " | ".join("" if v is None else str(v) for v in row)
                leaks.extend(_scan_bytes(blob.encode("utf-8"), path, canaries))
    finally:
        con.close()
    # Also scan the file bytes (pages / freelist) for anything not in row text.
    leaks.extend(_scan_bytes(path.read_bytes(), path, canaries))
    # Dedup by (canary_id, path, offset)
    seen: set[tuple[str, str, int | None]] = set()
    uniq: list[Leak] = []
    for leak in leaks:
        key = (leak.canary_id, leak.path, leak.offset)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(leak)
    return uniq


def scan_path(root: str | Path, secrets_path: str | Path | None = None) -> ScanResult:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(root)
    canaries = load_canaries(secrets_path)
    result = ScanResult()
    for path in _iter_files(root_path):
        result.scanned_files += 1
        suffix = path.suffix.lower()
        if suffix in BINARY_DB_SUFFIXES:
            result.leaks.extend(_scan_sqlite(path, canaries))
            continue
        data = path.read_bytes()
        # Always scan bytes; TEXT_SUFFIXES is documentation only.
        result.leaks.extend(_scan_bytes(data, path, canaries))
    return result


def format_report(result: ScanResult, max_details: int = 20) -> str:
    lines = [
        f"scanned_files={result.scanned_files}",
        f"leaks={len(result.leaks)}",
    ]
    # Summarize by canary id for quick grepping.
    if result.leaks:
        by_id: dict[str, int] = {}
        for leak in result.leaks:
            by_id[leak.canary_id] = by_id.get(leak.canary_id, 0) + 1
        summary = ", ".join(f"{k}×{v}" for k, v in sorted(by_id.items()))
        lines.append(f"by_id: {summary}")
    for i, leak in enumerate(result.leaks[:max_details], 1):
        lines.append(
            f"  [{i}] id={leak.canary_id} file={leak.path} "
            f"offset={leak.offset} value={leak.value_preview!r}"
        )
        if leak.context:
            lines.append(f"      context: …{leak.context}…")
    if len(result.leaks) > max_details:
        lines.append(f"  … {len(result.leaks) - max_details} more leak(s) omitted")
    if result.ok:
        lines.append("PASS: no canary leaks found")
    else:
        lines.append("FAIL: canary leak(s) detected — privacy regression")
    return "\n".join(lines)
