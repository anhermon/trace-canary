#!/usr/bin/env bash
# Local dogfood: redact run + scan (CI-equivalent), then print mcp-trace hint.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "== trace-canary self-test =="
python3 -m trace_canary self-test

echo ""
echo "== redact run → artifacts/ =="
rm -rf artifacts
python3 -m trace_canary run --mode redact --out artifacts
python3 -m trace_canary scan artifacts --sarif artifacts/canary.sarif

echo ""
echo "OK. Artifacts under $ROOT/artifacts"
if command -v mcp-trace >/dev/null 2>&1; then
  echo "mcp-trace on PATH: $(mcp-trace version 2>&1 | head -1)"
  echo "Dogfood tip: wrap the fixture with:"
  echo "  mcp-trace --stdio -- python3 -m trace_canary.fixture_server"
  echo "Point an MCP client at the proxy and scan your OTLP/JSONL export with:"
  echo "  python3 -m trace_canary scan /path/to/export"
else
  echo "mcp-trace not on PATH — install from https://github.com/anhermon/mcp-trace/releases"
fi
