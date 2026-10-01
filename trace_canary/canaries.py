"""Load and expose deterministic canary secrets."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent
# Prefer packaged data; fall back to repo-root canaries/ for editable checkouts.
DEFAULT_SECRETS = _PKG_DIR / "data" / "secrets.json"
if not DEFAULT_SECRETS.exists():
    DEFAULT_SECRETS = _PKG_DIR.parent / "canaries" / "secrets.json"


@lru_cache(maxsize=1)
def load_canaries(path: str | Path | None = None) -> list[dict[str, Any]]:
    secrets_path = Path(path) if path else DEFAULT_SECRETS
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    canaries = data.get("canaries") or []
    if not canaries:
        raise ValueError(f"no canaries defined in {secrets_path}")
    return canaries


def canary_values(path: str | Path | None = None) -> list[str]:
    return [c["value"] for c in load_canaries(path)]


def canary_by_id(cid: str, path: str | Path | None = None) -> str:
    for c in load_canaries(path):
        if c["id"] == cid:
            return str(c["value"])
    raise KeyError(cid)


def redact_text(text: str, path: str | Path | None = None) -> str:
    """Replace every known canary value with a stable placeholder."""
    out = text
    for c in load_canaries(path):
        val = str(c["value"])
        if val in out:
            out = out.replace(val, f"[REDACTED:{c['id']}]")
    return out
