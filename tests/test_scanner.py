from __future__ import annotations

import json
from pathlib import Path

from trace_canary.canaries import canary_by_id, canary_values
from trace_canary.runner import run_harness
from trace_canary.scanner import scan_path


def test_canary_catalog_size():
    assert len(canary_values()) >= 8


def test_raw_mode_leaks(tmp_path: Path):
    run_harness(tmp_path / "raw", mode="raw")
    result = scan_path(tmp_path / "raw")
    assert not result.ok
    ids = {leak.canary_id for leak in result.leaks}
    # At least arg + result + error vectors should show up.
    assert "api_key" in ids
    assert "error_detail" in ids
    assert "email" in ids or "ssn" in ids


def test_redact_mode_clean(tmp_path: Path):
    run_harness(tmp_path / "redact", mode="redact")
    result = scan_path(tmp_path / "redact")
    assert result.ok
    assert result.scanned_files >= 3


def test_scanner_finds_planted_file(tmp_path: Path):
    dirty = tmp_path / "note.txt"
    dirty.write_text(f"hello {canary_by_id('api_key')} world\n", encoding="utf-8")
    result = scan_path(tmp_path)
    assert not result.ok
    assert result.leaks[0].canary_id == "api_key"


def test_otlp_export_shape(tmp_path: Path):
    out = run_harness(tmp_path / "r", mode="redact")
    otlp = json.loads(Path(out["artifacts"]["otlp"]).read_text(encoding="utf-8"))
    assert "resourceSpans" in otlp
    spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) >= 5
