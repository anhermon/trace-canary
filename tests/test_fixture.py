from __future__ import annotations

from trace_canary.client import run_planted_session, start_fixture_server
from trace_canary.canaries import canary_by_id


def test_fixture_session_roundtrip():
    client = start_fixture_server()
    try:
        session = run_planted_session(client)
    finally:
        client.close()

    assert session["message_count"] >= 10
    assert len(session["tool_results"]) == 7
    # fail_with_secret should be an RPC error carrying the canary.
    fail = session["tool_results"][-1]["response"]
    assert "error" in fail
    assert canary_by_id("error_detail") in fail["error"]["message"]
