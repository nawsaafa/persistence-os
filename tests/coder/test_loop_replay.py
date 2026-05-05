"""Phase 2.2a G6 — audit-chain byte-identity replay for coder loop.

Three tests mirroring the 2.1c.6 G3 pattern (tests/http/test_audit_replay_byte_identity.py).
Positive: same scripted loop, same fixed-clock, two runs produce byte-identical audit
chains (last entry .id comparison, which encodes the full Merkle chain via prev_hash).
Negative #1: different path args in scripted decisions (handler swap) → args_hash differs
→ Merkle chain diverges. Negative #2: different fixed-clock timestamp → latency_ms /
recorded_at differ → entry content hash differs → Merkle chain diverges.

Clock-pinning approach (Option B from 2.1c.6):
    Substrate.open(audit=False) + install three handlers directly onto the
    substrate's own runtime via s.effect.install_handler():
      - raw no-op terminator (innermost — covers audit-only ops)
      - fixed clock handler (make_fixed_clock_handler(ts=FIXED_TS))
      - callable LLM handler (scripted decisions, innermost-after-raw)
      - fs handler (innermost-after-raw, position="bottom")
      - audit middleware (outermost, position="top")

    s.effect.perform routes through substrate._runtime (not the with_runtime
    ContextVar), so install_handler is the correct seam. with_runtime is still
    needed to ensure mask() can resolve _current() inside the audit handler.

Design ref: docs/plans/2026-04-30-phase-2-persistence-coder-design.md § G6.
Template:   tests/http/test_audit_replay_byte_identity.py (2.1c.6 G3 pattern).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from persistence.coder._session import Coder
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
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.sdk import Substrate

# Fixed timestamp — pinned so recorded_at / latency_ms are deterministic.
_FIXED_TS_A: float = 1_712_000_000.0
# Different timestamp for negative Test 3 (clock skew).
_FIXED_TS_B: float = 1_712_999_999.0


def _scripted_decisions(decisions: list[dict]):
    """Return a call_fn that yields one decision per call.

    make_callable_llm_handler calls call_fn with kwargs only:
    call_fn(model=..., messages=..., tools=..., temperature=..., max_tokens=...).
    """
    iterator = iter(decisions)

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {"tool_calls": [{"input": next(iterator)}], "text": ""}

    return _call_fn


def _make_decisions(project_root: Path, scratch_dir: Path) -> list[dict]:
    """Return the canonical 3-iter scripted decision set for the given paths."""
    return [
        {
            "kind": "act",
            "confidence": 0.9,
            "payload": {
                "op": ":fs/read",
                "args": {"path": str(project_root / "input.txt")},
            },
        },
        {
            "kind": "act",
            "confidence": 0.9,
            "payload": {
                "op": ":fs/write",
                "args": {
                    "path": str(scratch_dir / "out.txt"),
                    "bytes_or_text": "summary",
                },
            },
        },
        {
            "kind": "act",
            "confidence": 0.9,
            "payload": {
                "op": ":fs/read",
                "args": {"path": str(scratch_dir / "out.txt")},
                "done": True,
            },
        },
    ]


def _setup_dirs(root: Path) -> tuple[Path, Path]:
    """Create project_root and scratch_dir under root; populate input.txt."""
    project_root = root / "p"
    scratch_dir = root / "s"
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "input.txt").write_text("hello\n")
    return project_root, scratch_dir


def _install_pinned_audit_stack(
    s: Substrate,
    entries: list[AuditEntry],
    project_root: Path,
    scratch_dir: Path,
    decisions: list[dict],
    fixed_ts: float,
) -> None:
    """Install a canonical-equivalent audit stack with a fixed clock onto s.

    Five handlers installed in order (install_handler appends to
    the substrate's own runtime stack):
      position="bottom" (innermost): raw no-op terminator
      position="bottom" after raw: fixed clock
      position="bottom" after clock: fs handler (real side-effects)
      position="bottom" after fs: callable LLM handler (scripted decisions)
      position="top" (outermost): audit middleware

    Replaces make_system_clock_handler() with make_fixed_clock_handler(ts=...)
    so recorded_at and latency_ms are deterministic across runs.
    Ed25519 signer is NOT passed (default None) — entries are unsigned,
    content-hash only, fully deterministic.
    """
    raw = _make_canonical_raw_terminator()
    clock = make_fixed_clock_handler(ts=fixed_ts)
    fs = make_fs_handler(project_root=project_root, scratch_dir=scratch_dir)
    llm = make_callable_llm_handler(call_fn=_scripted_decisions(decisions))
    audit = make_audit_handler(entries, wraps=set(CANONICAL_AUDIT_WRAPPED_OPS))
    # Install innermost first; audit goes outermost (top).
    s.effect.install_handler(raw, position="bottom")
    s.effect.install_handler(clock, position="bottom")
    s.effect.install_handler(fs, position="bottom")
    s.effect.install_handler(llm, position="bottom")
    s.effect.install_handler(audit, position="top")


def test_coder_loop_audit_replay_byte_identity(tmp_path: Path) -> None:
    """Positive: two runs with pinned clock + identical args → identical Merkle chain tail.

    Substrate A and Substrate B are fresh in-memory substrates opened with
    audit=False so the substrate's own canonical stack (system clock) is not
    activated. A pinned-clock runtime is installed directly onto each substrate's
    own runtime via s.effect.install_handler() — this is the correct seam because
    s.effect.perform dispatches through substrate._runtime, not the with_runtime
    ContextVar.

    The assertion h1 == h2 is the G6 gate: if the Merkle chain incorporates
    wall-clock entropy (e.g. via an un-pinned system clock), the two entry ids
    will differ and this test fails.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    project_root_a, scratch_dir_a = _setup_dirs(dir_a)
    project_root_b, scratch_dir_b = _setup_dirs(dir_b)

    decisions_a = _make_decisions(project_root_a, scratch_dir_a)
    # Run B uses identical path shapes but in a DIFFERENT tmp subdir; however
    # we pin the SAME path strings in decisions to keep args_hash identical.
    # Use dir_b with same relative names so actual files exist and fs/read succeeds.
    decisions_b = _make_decisions(project_root_b, scratch_dir_b)

    # For byte-identity the path strings must be identical. Re-use dir_a paths
    # in both runs — dir_b dirs exist but we point both runs at dir_a files.
    # This ensures args_hash (canonical_hash of {"path": "..."}) is the same.
    decisions_b = _make_decisions(project_root_a, scratch_dir_a)

    # --- Substrate A ---
    entries_a: list[AuditEntry] = []
    s_a = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_a, entries_a, project_root_a, scratch_dir_a, decisions_a, _FIXED_TS_A
        )
        with with_runtime(s_a._runtime):
            coder_a = Coder(task="read input, write summary", substrate=s_a, max_iters=10)
            coder_a.run()
        assert len(entries_a) >= 1, (
            f"Expected at least one AuditEntry from run A; got {len(entries_a)}."
        )
        h1 = entries_a[-1].id
    finally:
        s_a.close()

    # --- Substrate B (fresh, same pinned conditions) ---
    # scratch_dir_a/out.txt was written by run A; run B will find it too.
    entries_b: list[AuditEntry] = []
    s_b = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_b, entries_b, project_root_a, scratch_dir_a, decisions_b, _FIXED_TS_A
        )
        with with_runtime(s_b._runtime):
            coder_b = Coder(task="read input, write summary", substrate=s_b, max_iters=10)
            coder_b.run()
        assert len(entries_b) >= 1, (
            f"Expected at least one AuditEntry from run B; got {len(entries_b)}."
        )
        h2 = entries_b[-1].id
    finally:
        s_b.close()

    # Both ids must be content-hash shaped.
    assert h1.startswith("sha256:"), f"entry A id is not sha256-prefixed: {h1!r}"
    assert h2.startswith("sha256:"), f"entry B id is not sha256-prefixed: {h2!r}"

    # G6: byte-identical replay invariant.
    assert h1 == h2, (
        f"Replay byte-identity FAILED: h1={h1!r} != h2={h2!r}.\n"
        "Possible causes:\n"
        "  1. Clock not fully pinned — check if latency_ms leaks wall time.\n"
        "  2. Ed25519 signature with session-unique key injected somewhere.\n"
        "  3. perform args or file contents differ between runs.\n"
        "  4. prev_hash mismatch — an entry count differs between runs."
    )


def test_coder_loop_audit_replay_handler_swap_mismatch(tmp_path: Path) -> None:
    """Negative #1: different path args in run 2 → args_hash differs → chains mismatch.

    Run A uses paths under dir_a, run B uses paths under dir_b with different
    absolute paths. The :fs/read and :fs/write audit entries will have different
    args_hash (canonical_hash({"path": "<path>"})) because the path strings differ.
    Since args_hash feeds into entry.id (content hash), and entry.id feeds into
    the next entry's prev_hash, the ENTIRE chain diverges from the first differing op.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    project_root_a, scratch_dir_a = _setup_dirs(dir_a)
    project_root_b, scratch_dir_b = _setup_dirs(dir_b)

    decisions_a = _make_decisions(project_root_a, scratch_dir_a)
    decisions_b = _make_decisions(project_root_b, scratch_dir_b)  # DIFFERENT paths

    # --- Run A ---
    entries_a: list[AuditEntry] = []
    s_a = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_a, entries_a, project_root_a, scratch_dir_a, decisions_a, _FIXED_TS_A
        )
        with with_runtime(s_a._runtime):
            coder_a = Coder(task="read input, write summary", substrate=s_a, max_iters=10)
            coder_a.run()
        assert len(entries_a) >= 1
        h1 = entries_a[-1].id
    finally:
        s_a.close()

    # --- Run B (different project_root → different path args) ---
    entries_b: list[AuditEntry] = []
    s_b = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_b, entries_b, project_root_b, scratch_dir_b, decisions_b, _FIXED_TS_A
        )
        with with_runtime(s_b._runtime):
            coder_b = Coder(task="read input, write summary", substrate=s_b, max_iters=10)
            coder_b.run()
        assert len(entries_b) >= 1
        h2 = entries_b[-1].id
    finally:
        s_b.close()

    assert h1.startswith("sha256:") and h2.startswith("sha256:")
    # The chains MUST differ because args contain different absolute paths.
    assert h1 != h2, (
        f"Expected chain mismatch after handler swap, but h1 == h2 == {h1!r}.\n"
        "The different :fs/read path args should have produced different args_hash "
        "values → different entry.id → diverging Merkle chains."
    )


def test_coder_loop_audit_replay_clock_skew_mismatch(tmp_path: Path) -> None:
    """Negative #2: different fixed-clock timestamp in run 2 → chains mismatch.

    Both runs use identical path args and file contents. Only the fixed-clock
    timestamp differs (_FIXED_TS_A vs _FIXED_TS_B). The audit handler reads
    :clock/now twice per op (pre- and post-call) to compute latency_ms and
    recorded_at. With a different fixed ts, both fields change → entry content
    hash differs → entry.id differs → chains diverge.
    """
    project_root, scratch_dir = _setup_dirs(tmp_path)

    decisions = _make_decisions(project_root, scratch_dir)

    # --- Run A (FIXED_TS_A) ---
    entries_a: list[AuditEntry] = []
    s_a = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_a, entries_a, project_root, scratch_dir, decisions, _FIXED_TS_A
        )
        with with_runtime(s_a._runtime):
            coder_a = Coder(task="read input, write summary", substrate=s_a, max_iters=10)
            coder_a.run()
        assert len(entries_a) >= 1
        h1 = entries_a[-1].id
    finally:
        s_a.close()

    # --- Run B (FIXED_TS_B — different timestamp) ---
    # decisions must be re-created since _scripted_decisions uses iter() (stateful).
    decisions2 = _make_decisions(project_root, scratch_dir)
    # scratch_dir/out.txt was written by run A; run B's :fs/write will overwrite it
    # (same content "summary"), and :fs/read will find the same bytes. The ONLY
    # difference is the clock value, which changes recorded_at and latency_ms.
    entries_b: list[AuditEntry] = []
    s_b = Substrate.open("memory", audit=False)
    try:
        _install_pinned_audit_stack(
            s_b, entries_b, project_root, scratch_dir, decisions2, _FIXED_TS_B
        )
        with with_runtime(s_b._runtime):
            coder_b = Coder(task="read input, write summary", substrate=s_b, max_iters=10)
            coder_b.run()
        assert len(entries_b) >= 1
        h2 = entries_b[-1].id
    finally:
        s_b.close()

    assert h1.startswith("sha256:") and h2.startswith("sha256:")
    # The chains MUST differ because recorded_at / latency_ms changed.
    assert h1 != h2, (
        f"Expected chain mismatch after clock skew, but h1 == h2 == {h1!r}.\n"
        "The different :clock/now values should have produced different recorded_at "
        "→ different entry.id → diverging Merkle chains.\n"
        f"TS_A={_FIXED_TS_A}, TS_B={_FIXED_TS_B}."
    )
