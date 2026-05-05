"""Phase 2.1c — Hypothesis property tests on HTTP layer (Design §10.6)."""
from __future__ import annotations

import hashlib

from hypothesis import HealthCheck, given, settings, strategies as st

# app_client fixture is provided by tests/http/conftest.py (yield + substrate teardown).


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

@st.composite
def _valid_tool_exec_payload(draw, session_id: str):
    """Draw a valid :claim/tool-exec attrs dict for the given session_id."""
    return {
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": draw(st.text(min_size=1, max_size=20)),
            "args": draw(st.dictionaries(
                keys=st.text(min_size=0, max_size=10),
                values=st.one_of(st.text(max_size=20), st.integers(), st.booleans()),
                max_size=5,
            )),
            "body_hash": None,
            "body_summary": draw(st.text(min_size=0, max_size=200)),
            "body_disposition": "inline",
            "started_at": draw(st.integers(min_value=0, max_value=2**40)),
            "duration_ms": draw(st.integers(min_value=0, max_value=10000)),
            "exit_code": draw(st.one_of(st.integers(min_value=0, max_value=255), st.none())),
            "session_id": session_id,
            "parent_correlation_id": None,
        },
    }


# ---------------------------------------------------------------------------
# Property 1 — Round-trip: emit → query returns an equivalent claim
# ---------------------------------------------------------------------------

@given(payload=_valid_tool_exec_payload(session_id="hp-roundtrip"))  # pyright: ignore[reportCallIssue]  # @st.composite strips `draw` arg, pyright doesn't model it
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_emit_query_roundtrip_property(app_client, payload):
    """Any valid :claim/tool-exec emit → query returns a claim with equivalent attrs (G6).

    The substrate accumulates across Hypothesis examples (same app_client fixture
    instance per pytest test), so we assert "at least one matching" rather than
    exact count.
    """
    emit = app_client.post("/v1/claim/emit", json={"claims": [payload]})
    assert emit.status_code == 200

    q = app_client.get(f"/v1/claim/query?session_id={payload['attrs']['session_id']}&limit=500")
    assert q.status_code == 200
    claims = q.json()["claims"]

    # At least one claim must match both the tool name and body_summary
    tool = payload["attrs"]["tool"]
    summary = payload["attrs"]["body_summary"]
    matching = [
        c for c in claims
        if c["attrs"]["tool"] == tool and c["attrs"]["body_summary"] == summary
    ]
    assert len(matching) >= 1, (
        f"No matching claim found for tool={tool!r}, body_summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Property 2 — Idempotency: any bytes blob put twice → same hash, duplicate=true
# ---------------------------------------------------------------------------

@given(content=st.binary(min_size=0, max_size=4096))
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_blob_put_idempotency_property(app_client, content):
    """Any bytes blob put twice → same hash on both responses, duplicate=true on second.

    If `content` happens to collide with a blob stored by a prior Hypothesis example
    within the same fixture lifetime, r1 may also report duplicate=true. That is
    acceptable — the property under test is that r2 is always duplicate, and both
    hashes agree with sha256(content).
    """
    expected_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    headers = {"Content-Type": "application/octet-stream", "X-Session-Id": "hp-blob"}

    r1 = app_client.post("/v1/blob/put", content=content, headers=headers)
    r2 = app_client.post("/v1/blob/put", content=content, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["hash"] == expected_hash
    assert r2.json()["hash"] == expected_hash
    # Second put of identical bytes is always duplicate
    assert r2.json()["duplicate"] is True


# ---------------------------------------------------------------------------
# Property 3 — Pagination: union of pages = full set, no duplicates
# ---------------------------------------------------------------------------

def test_pagination_union_property(app_client):
    """Emit N claims, paginate with various limits; union of pages = full set, no duplicates.

    Not Hypothesis-driven — pagination semantics are better verified deterministically
    because the substrate state and claim count are coupled to test execution order.
    Hypothesis-driven verification would require resetting state per example which is
    not compatible with the single-fixture-instance model.
    """
    N = 20
    for i in range(N):
        app_client.post("/v1/claim/emit", json={"claims": [{
            "kind": ":claim/tool-exec",
            "attrs": {
                "tool": f"T{i}",
                "args": {"i": i},
                "body_hash": None,
                "body_summary": str(i),
                "body_disposition": "inline",
                "started_at": 1714839028000 + i,
                "duration_ms": 0,
                "exit_code": 0,
                "session_id": "pg-test",
                "parent_correlation_id": None,
            },
        }]})

    for limit in (3, 5, 7, 50):
        seen_keys: set[tuple[int, str]] = set()
        since = 0
        while True:
            r = app_client.get(
                f"/v1/claim/query?session_id=pg-test&limit={limit}&since={since}"
            )
            assert r.status_code == 200
            body = r.json()
            page = body["claims"]
            if not page:
                break
            for c in page:
                key = (c["tx"], c["attrs"]["body_summary"])
                assert key not in seen_keys, (
                    f"limit={limit}: duplicate claim across pages: tx={c['tx']}"
                )
                seen_keys.add(key)
            next_since = body["next_since"]
            # Advance cursor; stop when next_since does not move or page is under-full
            if next_since == since or len(page) < limit:
                break
            since = next_since

        assert len(seen_keys) == N, (
            f"limit={limit}: expected {N} unique claims, got {len(seen_keys)}"
        )
