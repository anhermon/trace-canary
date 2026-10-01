# trace-canary

[![CI](https://github.com/anhermon/trace-canary/actions/workflows/ci.yml/badge.svg)](https://github.com/anhermon/trace-canary/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)

**Runtime privacy regression harness for agent/MCP observability.**

Plant deterministic secrets (*canaries*) into MCP tool inputs, results, and errors.
Drive a short session. Recursively scan traces, replays, and exports
(JSONL / OTLP JSON / SQLite). **Any canary hit → exit 1** (optional SARIF).

This is a **CI harness**, not a dashboard. Pair it with
[mcp-trace](https://github.com/anhermon/mcp-trace) and
[agent-obs-lab](https://github.com/anhermon/agent-obs-lab).

```
MCP client  →  fixture MCP (plants canaries)
                    ↓
              recorder / mcp-trace → JSONL · OTLP · SQLite
                    ↓
              trace-canary scan  →  PASS or FAIL (+ SARIF)
```

## Why

Agent stacks happily dump tool arguments and error strings into spans.
Credentials, connection strings, JWTs, and PII then land in Jaeger, Tempo,
Phoenix, langfuse exports, and “debug” JSONL. Unit tests rarely catch that.

Canaries make the failure mode **loud and deterministic**:

| Vector | How the fixture plants it |
|--------|---------------------------|
| Tool arguments | `store_credential`, `connect_db`, `register_webhook`, `echo_secret` |
| Tool results | `echo_secret`, `lookup_profile`, `connect_db` |
| Tool / RPC errors | `fail_with_secret` |

Twelve fixed strings live in [`canaries/secrets.json`](canaries/secrets.json).
If any of them appear in an artifact, CI fails.

## Quick start (local)

Requirements: **Python 3.10+** (stdlib only — **no runtime deps**).

The primary proof path is the stdlib self-test / `./scripts/run-canary.sh`.
**pytest is optional** (dev only: `pip install -e '.[dev]' && pytest`); CI installs
it for unit tests, but you do not need pytest for day-to-day or CI-equivalent runs.

```bash
git clone https://github.com/anhermon/trace-canary
cd trace-canary

# Prove the scanner works (raw must leak; redact must pass)
python3 -m trace_canary self-test

# CI-equivalent path: sanitized export + scan
./scripts/run-canary.sh
# or:
python3 -m trace_canary run --mode redact --out artifacts
python3 -m trace_canary scan artifacts --sarif artifacts/canary.sarif
```

Exit codes:

| Command | `0` | `1` |
|---------|-----|-----|
| `run` | artifacts written | harness error |
| `scan` | no canaries found | ≥1 leak |
| `self-test` | raw leaks **and** redact is clean | either check wrong |

## CLI

```bash
python3 -m trace_canary run [--mode redact|raw] [--out artifacts]
python3 -m trace_canary scan <path> [--sarif out.sarif]
python3 -m trace_canary self-test [--sarif raw.sarif] [--keep]
```

- **`redact`** (default for CI) — recorder replaces known canaries with
  `[REDACTED:<id>]` before writing JSONL / OTLP JSON / SQLite. Models a
  well-behaved exporter (`capture_tool_args: false`, error scrubbing).
- **`raw`** — deliberately persists full args/results/errors. Used by
  `self-test` to prove the scanner catches leaks.

## Dogfood against mcp-trace / agent-obs-lab

1. Install [mcp-trace](https://github.com/anhermon/mcp-trace) (release binary or
   `go install …@v2.0.3`). Keep **`capture_tool_args: false`** (default).
2. Wrap the fixture. **`--stdio` means the upstream is stdio** (the fixture).
   Your MCP client must speak **HTTP to mcp-trace’s `--port`** — do **not** pipe
   JSON-RPC into mcp-trace’s stdin (that hangs; stdin is for the child process).

   ```bash
   # Default listen port is 8001; use --port if it is busy (dogfood often uses 18021).
   mcp-trace --stdio --port 18021 --otel-stdout --include-lifecycle -- \
     python3 -m trace_canary.fixture_server
   ```

   Topology:

   ```
   HTTP MCP client  →  http://127.0.0.1:<port>/  →  mcp-trace  →  fixture (stdio)
   ```

3. Export spans (`--otel-stdout`, OTLP collector file exporter, Jaeger dump, or
   your JSONL pipeline — whatever you already ship from
   [agent-obs-lab](https://github.com/anhermon/agent-obs-lab)).
4. Scan the export:

   ```bash
   python3 -m trace_canary scan /path/to/otlp-or-jsonl --sarif leaks.sarif
   ```

**One-shot smoke** (starts proxy, POSTs `initialize` / `tools/list` /
`tools/call`, scans `--otel-stdout`):

```bash
./scripts/wrap-mcp-trace-smoke.sh
# Optional: TRACE_CANARY_WRAP_PORT=18021 TRACE_CANARY_WRAP_OUT=./artifacts-mcp-trace-wrap
```

If mcp-trace (or your stack) ever starts recording full tool arguments / raw
error bodies, canaries from `canaries/secrets.json` will light up.

The built-in recorder embeds an `mcp_trace` probe in `summary.json`
(`on_path`, version) so CI logs show whether the proxy is available. That probe
is informational only — it does **not** wrap the fixture. Use the commands
above (or `./scripts/wrap-mcp-trace-smoke.sh`) for real wrap dogfood.

## Artifacts written by `run`

| File | Contents |
|------|----------|
| `session.jsonl` | Every JSON-RPC message (direction + body) |
| `otlp-export.json` | Pseudo OTLP JSON with one span per `tools/call` |
| `traces.sqlite` | `messages` + `spans` tables |
| `summary.json` | Mode, counts, mcp-trace probe |

## GitHub Actions

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on push/PR:

1. `python -m trace_canary self-test`
2. `run --mode redact` + `scan` (must pass)
3. Uploads SARIF from the raw self-test leg for inspection (not enforced as a
   blocking code-scanning upload — keeps the workflow free of extra permissions)
4. Optional unit tests via `pip install pytest && pytest` (not required locally)

## Design notes

- **No dashboard** — exit codes + optional SARIF only.
- **No runtime dependencies** — fixture, client, recorder, and scanner are
  stdlib Python so the harness itself cannot become a supply-chain footgun.
- **Deterministic canaries** — stable strings, not random UUIDs, so failures
  are greppable across runs and repos.
- **Recursive scan** — walks directories; SQLite is queried *and* byte-scanned.

## License

[MIT](LICENSE) © Angel Hermon
