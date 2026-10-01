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

Requirements: **Python 3.10+** (stdlib only — no runtime deps).

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
2. Wrap the fixture:

   ```bash
   mcp-trace --stdio -- python3 -m trace_canary.fixture_server
   # client → mcp-trace :8001 → fixture (stdio)
   ```

3. Export spans (OTLP collector file exporter, Jaeger dump, or your JSONL
   pipeline — whatever you already ship from
   [agent-obs-lab](https://github.com/anhermon/agent-obs-lab)).
4. Scan the export:

   ```bash
   python3 -m trace_canary scan /path/to/otlp-or-jsonl --sarif leaks.sarif
   ```

If mcp-trace (or your stack) ever starts recording full tool arguments / raw
error bodies, canaries from `canaries/secrets.json` will light up.

The built-in recorder also embeds an `mcp_trace` probe in `summary.json`
(`on_path`, version) so CI logs show whether the proxy is available.

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

## Design notes

- **No dashboard** — exit codes + optional SARIF only.
- **No runtime dependencies** — fixture, client, recorder, and scanner are
  stdlib Python so the harness itself cannot become a supply-chain footgun.
- **Deterministic canaries** — stable strings, not random UUIDs, so failures
  are greppable across runs and repos.
- **Recursive scan** — walks directories; SQLite is queried *and* byte-scanned.

## License

[MIT](LICENSE) © Angel Hermon
