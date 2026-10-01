"""CLI: python -m trace_canary {run,scan,self-test}."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from trace_canary import __version__
from trace_canary.runner import run_harness
from trace_canary.sarif import write_sarif
from trace_canary.scanner import format_report, scan_path


def _cmd_run(args: argparse.Namespace) -> int:
    result = run_harness(args.out, mode=args.mode)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    result = scan_path(args.path)
    print(format_report(result))
    if args.sarif:
        write_sarif(result, args.sarif)
        print(f"wrote SARIF: {args.sarif}")
    return 0 if result.ok else 1


def _cmd_self_test(args: argparse.Namespace) -> int:
    """Prove scanner catches leaks (raw) and redacting exporter stays clean."""
    base = Path(tempfile.mkdtemp(prefix="trace-canary-selftest-"))
    try:
        raw_dir = base / "raw"
        clean_dir = base / "redact"

        print("== self-test: raw mode (expect FAIL / leaks) ==")
        run_harness(raw_dir, mode="raw")
        raw = scan_path(raw_dir)
        print(format_report(raw))
        if raw.ok:
            print("SELF-TEST FAILED: raw mode produced no leaks (scanner broken?)")
            return 1

        print("\n== self-test: redact mode (expect PASS) ==")
        run_harness(clean_dir, mode="redact")
        clean = scan_path(clean_dir)
        print(format_report(clean))
        if not clean.ok:
            print("SELF-TEST FAILED: redact mode still leaked canaries")
            return 1

        if args.sarif:
            write_sarif(raw, args.sarif)
            print(f"wrote raw-mode SARIF (for inspection): {args.sarif}")

        print("\nSELF-TEST PASSED")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)
        else:
            print(f"kept artifacts under {base}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trace-canary",
        description="Runtime privacy regression harness for agent/MCP observability.",
    )
    p.add_argument("--version", action="version", version=f"trace-canary {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Drive fixture MCP session and write artifacts")
    run_p.add_argument(
        "--mode",
        choices=("redact", "raw"),
        default="redact",
        help="redact=sanitize canaries before write (CI default); raw=deliberate leak",
    )
    run_p.add_argument(
        "--out",
        default="artifacts",
        help="Output directory for JSONL / OTLP JSON / SQLite (default: artifacts)",
    )
    run_p.set_defaults(func=_cmd_run)

    scan_p = sub.add_parser("scan", help="Recursively scan artifacts for canary leaks")
    scan_p.add_argument("path", help="File or directory to scan")
    scan_p.add_argument("--sarif", help="Optional SARIF 2.1.0 output path")
    scan_p.set_defaults(func=_cmd_scan)

    st = sub.add_parser(
        "self-test",
        help="Run raw (must leak) + redact (must pass) end-to-end checks",
    )
    st.add_argument("--sarif", help="Write SARIF from the raw (leaky) run")
    st.add_argument(
        "--keep",
        action="store_true",
        help="Keep temporary artifact dirs after the test",
    )
    st.set_defaults(func=_cmd_self_test)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
