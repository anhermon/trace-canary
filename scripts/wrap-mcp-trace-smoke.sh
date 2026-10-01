#!/usr/bin/env bash
# One-shot dogfood: HTTP MCP client → mcp-trace (--port) → fixture (stdio upstream),
# capture --otel-stdout, then scan. Does NOT pipe JSON-RPC into mcp-trace stdin.
#
# Requires: mcp-trace on PATH, curl, python3.
# Optional: TRACE_CANARY_WRAP_PORT (default 18021; default mcp-trace :8001 is often busy).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if ! command -v mcp-trace >/dev/null 2>&1; then
  echo "mcp-trace not on PATH — install from https://github.com/anhermon/mcp-trace/releases" >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for the HTTP MCP client leg" >&2
  exit 2
fi

PORT="${TRACE_CANARY_WRAP_PORT:-18021}"
OUT="${TRACE_CANARY_WRAP_OUT:-$ROOT/artifacts-mcp-trace-wrap}"
rm -rf "$OUT"
mkdir -p "$OUT"

echo "== wrap smoke: mcp-trace --stdio --port $PORT → fixture =="
echo "Client transport: HTTP POST http://127.0.0.1:${PORT}/ (not mcp-trace stdin)"

mcp-trace --stdio --port "$PORT" --otel-stdout --include-lifecycle \
  --service-name trace-canary-wrap-smoke \
  -- python3 -m trace_canary.fixture_server \
  >"$OUT/otel-stdout.txt" 2>"$OUT/proxy.stderr" &
PROXY_PID=$!

cleanup() {
  if kill -0 "$PROXY_PID" 2>/dev/null; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Wait until the port accepts connections (or proxy exits).
for _ in $(seq 1 50); do
  if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "mcp-trace exited before listen; stderr:" >&2
    cat "$OUT/proxy.stderr" >&2 || true
    exit 1
  fi
  if curl -sS -o /dev/null --connect-timeout 0.2 "http://127.0.0.1:${PORT}/" 2>/dev/null; then
    break
  fi
  # Even a refused connection means we're still waiting; "000" from curl -w is fine.
  sleep 0.1
done

post_rpc() {
  local id="$1" method="$2" params="${3:-}"
  local body
  if [[ -n "$params" ]]; then
    body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":%s}' "$id" "$method" "$params")
  else
    body=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s"}' "$id" "$method")
  fi
  curl -sS --fail-with-body \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    -X POST "http://127.0.0.1:${PORT}/" \
    -d "$body"
}

echo "-- initialize --"
INIT=$(post_rpc 1 initialize '{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"trace-canary-wrap-smoke","version":"0.1.0"}}')
printf '%s\n' "$INIT" | tee "$OUT/initialize.json" >/dev/null
# Best-effort notification (no response expected).
curl -sS -o /dev/null \
  -H 'Content-Type: application/json' \
  -X POST "http://127.0.0.1:${PORT}/" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' || true

echo "-- tools/list --"
LIST=$(post_rpc 2 tools/list)
printf '%s\n' "$LIST" | tee "$OUT/tools-list.json" >/dev/null

# Real canary value so a misconfigured capture_tool_args=true would fail the scan.
API_KEY=$(python3 -c 'from trace_canary.canaries import canary_by_id; print(canary_by_id("api_key"))')
CALL_PARAMS=$(python3 -c 'import json,sys; print(json.dumps({"name":"echo_secret","arguments":{"secret":sys.argv[1],"label":"api_key"}}))' "$API_KEY")

echo "-- tools/call echo_secret --"
CALL=$(post_rpc 3 tools/call "$CALL_PARAMS")
printf '%s\n' "$CALL" | tee "$OUT/tools-call.json" >/dev/null

cleanup
trap - EXIT

otel_bytes=$(wc -c <"$OUT/otel-stdout.txt" | tr -d ' ')
if [[ "$otel_bytes" -lt 1 ]]; then
  echo "no --otel-stdout spans captured; proxy.stderr:" >&2
  cat "$OUT/proxy.stderr" >&2 || true
  exit 1
fi

echo ""
echo "== scan otel-stdout (expect PASS with capture_tool_args=false) =="
python3 -m trace_canary scan "$OUT/otel-stdout.txt" --sarif "$OUT/leaks.sarif"

python3 - <<PY
import json
from pathlib import Path
out = Path("$OUT")
summary = {
    "port": int("$PORT"),
    "client": "HTTP POST / (streamable JSON-RPC)",
    "upstream": "stdio: python3 -m trace_canary.fixture_server",
    "otel_bytes": (out / "otel-stdout.txt").stat().st_size,
    "initialize_ok": "result" in json.loads((out / "initialize.json").read_text()),
    "tools_call_ok": "result" in json.loads((out / "tools-call.json").read_text()),
}
(out / "wrap-result.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
PY

echo ""
echo "OK. Artifacts under $OUT"
