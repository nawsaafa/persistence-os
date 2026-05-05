"""Phase 2.1c.6 G3 — replay byte-identity for :claim/emit audit chain.

Two :claim/emit performs on fresh substrates with pinned clock + identical
args must produce byte-identical entries[-1].id. This proves the audit chain
is replay-deterministic when underlying inputs are pinned — load-bearing for
the substrate's "counterfactual replay" invariant.

Design refs: docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md
             § 5.3 replay-determinism risks, § 6 G3.

Clock-pinning approach (Option B):
    Substrate.open(audit=False) + install three handlers directly onto the
    substrate's own runtime via s.effect.install_handler():
      - raw no-op terminator (innermost)
      - fixed clock handler (make_fixed_clock_handler(ts=FIXED_TS))
      - audit middleware (make_audit_handler writing into a test-owned list)
    s.effect.perform routes through substrate._runtime (not the with_runtime
    ContextVar), so install_handler is the correct seam.

    Option A (canonical_audit_stack + override) was not used because
    Substrate.open(audit=True) calls runtime._active.set directly with the
    system-clock stack before this test can intercept it, requiring teardown
    coordination. Option B avoids that complexity.

Ed25519 signing:
    canonical_audit_stack calls make_audit_handler(entries, wraps=...) with NO
    signer argument — signer defaults to None, entries are unsigned, so the
    entry id is purely a content-hash and is deterministic given fixed inputs.
    No keypair pinning needed.
"""
from __future__ import annotations

import datetime as dt

from persistence.effect import (
    AuditEntry,
    make_audit_handler,
    make_fixed_clock_handler,
    with_runtime,
)
from persistence.effect._audit_stack import (
    CANONICAL_AUDIT_WRAPPED_OPS,
    _make_canonical_raw_terminator,
)
from persistence.sdk import Substrate

# Fixed timestamp — pinned so recorded_at / latency_ms are deterministic.
_FIXED_TS: float = 1_712_000_000.0

# Fixed datom inputs — same args for both substrate runs.
_FIXED_CLAIM_E = "replay-test-entity-001"
_FIXED_CLAIM_A = ":claim/tool-exec"
_FIXED_CLAIM_V = '{"tool":"replay-probe"}'
_FIXED_VALID_FROM = dt.datetime(2024, 4, 1, 0, 0, 0, tzinfo=dt.timezone.utc)

# Fixed perform args — mirrors what the HTTP route passes after T2 wiring.
_FIXED_PERFORM_ARGS: dict = {
    "claim_ids": ["replay-test-claim-id-001"],
    "tx": 1,
    "kind_counts": {":claim/tool-exec": 1},
}


def _install_pinned_audit_stack(s: Substrate, entries: list[AuditEntry]) -> None:
    """Install a canonical-equivalent audit stack with a fixed clock onto s.

    Three handlers installed in order (install_handler appends to
    the substrate's own runtime stack):
      position="bottom" (innermost): raw no-op terminator
      position="bottom" after raw: fixed clock
      position="top" (outermost): audit middleware

    Replaces make_system_clock_handler() with make_fixed_clock_handler(ts=...)
    so recorded_at and latency_ms are deterministic across runs.
    Ed25519 signer is NOT passed (default None) — entries are unsigned,
    content-hash only, fully deterministic.
    """
    raw = _make_canonical_raw_terminator()
    clock = make_fixed_clock_handler(ts=_FIXED_TS)
    audit = make_audit_handler(entries, wraps=set(CANONICAL_AUDIT_WRAPPED_OPS))
    # Install innermost first; audit goes outermost (top).
    s.effect.install_handler(raw, position="bottom")
    s.effect.install_handler(clock, position="bottom")
    s.effect.install_handler(audit, position="top")


def test_claim_emit_audit_replay_byte_identity() -> None:
    """Two :claim/emit performs with pinned clock + identical args → identical entry id.

    Substrate A and Substrate B are fresh in-memory substrates opened with
    audit=False so the substrate's own canonical stack (system clock) is not
    activated. A pinned-clock runtime is installed directly onto each substrate's
    own runtime via s.effect.install_handler() — this is the correct seam because
    s.effect.perform dispatches through substrate._runtime, not the with_runtime
    ContextVar.

    The assertion h1 == h2 is the G3 gate: if the Merkle chain incorporates
    wall-clock entropy (e.g. via an un-pinned system clock), the two entry ids
    will differ and this test fails.
    """
    datom = {
        "e": _FIXED_CLAIM_E,
        "a": _FIXED_CLAIM_A,
        "v": _FIXED_CLAIM_V,
        "valid_from": _FIXED_VALID_FROM,
    }

    # --- Substrate A ---
    entries_a: list[AuditEntry] = []
    s_a = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(s_a, entries_a)
        # with_runtime activates substrate._runtime in the ContextVar so the
        # audit middleware's internal mask() call can resolve _current().
        with with_runtime(s_a._runtime):
            s_a.fact.transact([datom])
            s_a.effect.perform(":claim/emit", _FIXED_PERFORM_ARGS)
        assert len(entries_a) >= 1, (
            "Expected at least one AuditEntry after :claim/emit on substrate A; "
            f"got {len(entries_a)}. Check T2 wiring in persistence/http/routes/claim.py."
        )
        h1 = entries_a[-1].id
    finally:
        s_a.close()

    # --- Substrate B (fresh, same pinned conditions) ---
    entries_b: list[AuditEntry] = []
    s_b = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(s_b, entries_b)
        with with_runtime(s_b._runtime):
            s_b.fact.transact([datom])
            s_b.effect.perform(":claim/emit", _FIXED_PERFORM_ARGS)
        assert len(entries_b) >= 1, (
            "Expected at least one AuditEntry after :claim/emit on substrate B; "
            f"got {len(entries_b)}. Check T2 wiring in persistence/http/routes/claim.py."
        )
        h2 = entries_b[-1].id
    finally:
        s_b.close()

    # Both ids must be content-hash shaped.
    assert h1.startswith("sha256:"), (
        f"entry A id is not sha256-prefixed: {h1!r}"
    )
    assert h2.startswith("sha256:"), (
        f"entry B id is not sha256-prefixed: {h2!r}"
    )

    # G3: byte-identical replay invariant.
    assert h1 == h2, (
        f"Replay byte-identity FAILED: h1={h1!r} != h2={h2!r}.\n"
        "Possible causes:\n"
        "  1. Clock not fully pinned — check if latency_ms leaks wall time.\n"
        "  2. Ed25519 signature with session-unique key injected somewhere.\n"
        "  3. perform args or datom inputs differ between runs.\n"
        "  4. prev_hash mismatch — the entry is not the first in its chain "
        "     (prev_hash would differ if a prior entry exists)."
    )
