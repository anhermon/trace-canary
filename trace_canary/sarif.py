"""Optional SARIF 2.1.0 emitter for canary leak findings."""

from __future__ import annotations

import json
from pathlib import Path

from trace_canary.scanner import ScanResult


def to_sarif(result: ScanResult, tool_name: str = "trace-canary") -> dict:
    rules = [
        {
            "id": "canary-leak",
            "shortDescription": {"text": "Planted privacy canary found in artifact"},
            "fullDescription": {
                "text": (
                    "A deterministic secret planted by the trace-canary fixture "
                    "appeared in a trace, replay, or export artifact."
                )
            },
            "defaultConfiguration": {"level": "error"},
            "helpUri": "https://github.com/anhermon/trace-canary",
        }
    ]
    results = []
    for leak in result.leaks:
        results.append(
            {
                "ruleId": "canary-leak",
                "level": "error",
                "message": {
                    "text": (
                        f"Canary '{leak.canary_id}' leaked in {leak.path} "
                        f"(value preview: {leak.value_preview})"
                    )
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": leak.path},
                            "region": {
                                "charOffset": leak.offset if leak.offset is not None else 0
                            },
                        }
                    }
                ],
                "properties": {
                    "canaryId": leak.canary_id,
                    "context": leak.context,
                },
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "informationUri": "https://github.com/anhermon/trace-canary",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def write_sarif(result: ScanResult, path: str | Path) -> Path:
    out = Path(path)
    out.write_text(json.dumps(to_sarif(result), indent=2) + "\n", encoding="utf-8")
    return out
