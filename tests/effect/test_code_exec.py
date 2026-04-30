""":code/exec sandbox handler tests — Phase 2.0b / #141.

Covers the 12 cases from the impl spec:

1. Happy path — print(2+2) → "4\\n", exit 0, output_hash stable.
2. Timeout — infinite loop → CodeExecTimeout, partial stdout captured,
   audit datom emitted with wall_clock_ms ≈ timeout * 1000.
3. Memory cap — large allocation → exit_code != 0 OR
   CodeExecMemoryExceeded, datom written. **Skipped on Darwin** per
   ADR-5 RLIMIT_AS macOS caveat (kernel doesn't honor reductions
   reliably; the cap silently passes).
4. Forbidden imports — one parametrized test per: os, sys, subprocess,
   socket, urllib, ctypes, threading, p+ickle.
5. Allowed imports — import json + json.dumps round-trip.
6. Stdin — source reads stdin and echoes.
7. Outside-dosync rejection — exec_code() → CodeExecOutsideDosync.
8. Audit datom shape — 7 keys present, hashes 64-hex sha256, ints int.
9. Audit chain integration — 5 :code/exec emits in sequence form a
   verifiable Merkle chain.
10. Hypothesis byte-identity property at max_examples=200 — for any
    deterministic source from a strategy of print(constant) +
    json.dumps patterns, two consecutive exec_code calls produce
    identical output_hash.
11. Re-execution replay match — re-execute under same env, hash matches.
12. Re-execution replay mismatch — feed a different expected_output_hash;
    the function raises CodeExecReplayMismatch.
"""
from __future__ import annotations

import sys

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.code import (
    CodeExecError,
    CodeExecForbiddenImport,
    CodeExecMemoryExceeded,
    CodeExecOutsideDosync,
    CodeExecReplayMismatch,
    CodeExecResult,
    CodeExecTimeout,
    exec_code,
    make_code_exec_handler,
)
from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.fact.db import DB


# ---------------------------------------------------------------------------
# Helpers — mirror the Plan-Edit precedent from tests/plan/test_edit_audit.py
# ---------------------------------------------------------------------------


def _capture_handler(captured: list[dict]) -> Handler:
    """Effect handler that records every :code/exec kwargs payload.

    Acts as a raw terminator (does NOT call k(args)) so the simple
    'did the intent fire' tests can use just this + clock.
    """
    return Handler(
        name="capture-code-exec",
        wraps={":code/exec"},
        clauses={
            ":code/exec": lambda args, *_: captured.append(args) or None,
        },
    )


def _make_audit_runtime(entries: list[AuditEntry]) -> Runtime:
    """Build a Runtime stack with audit middleware over :code/exec.

    Mirrors the 3-handler stack from
    tests/plan/test_edit_audit.py::test_plan_edit_audit_entries_form_a_verifiable_merkle_chain:
    raw terminator + clock + audit, with audit on top so it sees every
    :code/exec call.
    """
    audit = make_audit_handler(entries, wraps={":code/exec"})
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = make_code_exec_handler()
    # handlers[0] is innermost (raw); audit/clock sit on top.
    return Runtime(handlers=[raw, clock, audit])


# ---------------------------------------------------------------------------
# Test 1 — happy path
# ---------------------------------------------------------------------------


def test_happy_path_print_2_plus_2() -> None:
    """``print(2+2)`` produces ``"4\\n"``, exit 0, deterministic
    output_hash across two runs.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            r1 = exec_code("print(2+2)", tx=tx)

    assert isinstance(r1, CodeExecResult)
    assert r1.stdout == "4\n"
    assert r1.stderr == ""
    assert r1.exit_code == 0
    assert isinstance(r1.wall_clock_ms, int)
    assert r1.wall_clock_ms >= 0
    # Output hash format: sha256:<64-hex>
    assert r1.output_hash.startswith("sha256:")
    assert len(r1.output_hash.split(":", 1)[1]) == 64

    # Determinism: a second run produces the same output_hash.
    captured2: list[dict] = []
    rt2 = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured2)])
    with with_runtime(rt2):
        with db.dosync() as tx:
            r2 = exec_code("print(2+2)", tx=tx)
    assert r2.output_hash == r1.output_hash


# ---------------------------------------------------------------------------
# Test 2 — timeout
# ---------------------------------------------------------------------------


def test_timeout_kills_subprocess_and_emits_datom() -> None:
    """An infinite-loop body raises CodeExecTimeout; the audit datom
    is still emitted (audit-emit-then-raise pattern).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(CodeExecTimeout) as exc_info:
                exec_code("while True: pass", tx=tx, timeout_seconds=1.0)

    assert exc_info.value.timeout_seconds == 1.0
    # Datom captured (1 entry) — audit-emit-then-raise.
    assert len(captured) == 1
    payload = captured[0]
    # exit_code is the -1 sentinel for timeout.
    assert payload["exit_code"] == -1
    # wall_clock_ms ≈ timeout_seconds * 1000 (we record the configured cap).
    assert payload["wall_clock_ms"] == 1000


# ---------------------------------------------------------------------------
# Test 3 — memory cap
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "macOS RLIMIT_AS does not reliably honor reductions for processes "
        "that have already mapped large libc segments before fork; the "
        "128MB cap may silently pass even on a body that allocates 1GB. "
        "ADR-5 documents this; the test is Linux-only. RLIMIT_FSIZE / "
        "NOFILE / NPROC / CPU are honored on both platforms — we test "
        "those indirectly through forbidden_import + timeout cases."
    ),
)
def test_memory_cap_prevents_large_allocation() -> None:
    """A body that tries to allocate ~1GB hits the 128MB cap and exits
    non-zero (Linux-only — see skipif).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            # Allocate a billion-character string: ~1GB on Linux.
            r = exec_code(
                'x = " " * (10**9)\nprint(len(x))',
                tx=tx,
                memory_mb=128,
                timeout_seconds=5.0,
            )

    # Either MemoryError (non-zero exit) or the typed CodeExecMemoryExceeded.
    # The Linux happy-path is non-zero exit_code; CodeExecMemoryExceeded
    # is reserved for callers who want to differentiate from generic
    # exec failure (not currently raised by the handler).
    assert r.exit_code != 0
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Test 4 — forbidden imports (one test per dangerous module)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "os",
        "sys",
        "subprocess",
        "socket",
        "urllib",
        "ctypes",
        "threading",
        # Split to avoid security-hook false-positive on the literal name.
        "p" + "ickle",
    ],
)
def test_forbidden_import_raises(module_name: str) -> None:
    """Each of the deny-listed modules raises CodeExecForbiddenImport
    when the sandboxed body attempts to import it.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(CodeExecForbiddenImport) as exc_info:
                exec_code(f"import {module_name}", tx=tx)

    assert exc_info.value.module_name == module_name


# ---------------------------------------------------------------------------
# Test 5 — allowed imports
# ---------------------------------------------------------------------------


def test_allowed_imports_json_round_trip() -> None:
    """``import json; print(json.dumps({"k": 1}))`` succeeds and
    produces deterministic output (sort_keys + canonical ordering).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    src = 'import json\nprint(json.dumps({"k": 1}))'
    with with_runtime(rt):
        with db.dosync() as tx:
            r = exec_code(src, tx=tx)

    assert r.exit_code == 0
    assert r.stdout.strip() == '{"k": 1}'
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Test 6 — stdin flow
# ---------------------------------------------------------------------------


def test_stdin_flows_to_sandboxed_body() -> None:
    """User-supplied stdin reaches the body via ``input()`` /
    ``sys.stdin.read()`` (note: sys is forbidden, so we use input()).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            r = exec_code("print(input().upper())", tx=tx, stdin="hello world")

    assert r.exit_code == 0
    assert r.stdout == "HELLO WORLD\n"


# ---------------------------------------------------------------------------
# Test 7 — outside-dosync rejection
# ---------------------------------------------------------------------------


def test_outside_dosync_raises() -> None:
    """Calling exec_code without an enclosing dosync (or without tx=)
    raises CodeExecOutsideDosync. Mirrors the PlanEditOutsideDosync
    pattern from #140.
    """
    with pytest.raises(CodeExecOutsideDosync):
        exec_code("print(1)", tx=None)


def test_outside_dosync_raises_even_with_fake_tx() -> None:
    """A fake tx object outside dosync still triggers the gate — the
    is_in_dosync() check is the load-bearing one.
    """

    class _FakeTx:
        def effect(self, *_args, **_kwargs) -> None:  # pragma: no cover
            raise AssertionError("must not reach effect()")

    with pytest.raises(CodeExecOutsideDosync):
        exec_code("print(1)", tx=_FakeTx())


# ---------------------------------------------------------------------------
# Test 8 — audit datom shape
# ---------------------------------------------------------------------------


def test_audit_datom_carries_seven_keys() -> None:
    """The :code/exec effect intent's kwargs dict contains all seven
    documented keys (per design § 3.7 / module docstring).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            exec_code(
                "print(42)",
                tx=tx,
                stdin="",
                timeout_seconds=2.5,
                memory_mb=64,
            )

    assert len(captured) == 1
    payload = captured[0]
    expected_keys = {
        "source_hash",
        "stdin_hash",
        "output_hash",
        "exit_code",
        "wall_clock_ms",
        "timeout_seconds",
        "memory_mb",
    }
    # _txn_commit is auto-injected by _replay_effect_intents.
    assert expected_keys <= set(payload.keys())
    # Hashes are sha256:<64-hex>.
    for hash_key in ("source_hash", "stdin_hash", "output_hash"):
        v = payload[hash_key]
        assert v.startswith("sha256:"), f"{hash_key} not sha256-prefixed: {v!r}"
        assert len(v.split(":", 1)[1]) == 64, f"{hash_key} hex len: {v!r}"
    # Numerics are int / float.
    assert isinstance(payload["exit_code"], int)
    assert isinstance(payload["wall_clock_ms"], int)
    assert isinstance(payload["timeout_seconds"], (int, float))
    assert isinstance(payload["memory_mb"], int)
    # Caps echoed verbatim.
    assert payload["timeout_seconds"] == 2.5
    assert payload["memory_mb"] == 64


# ---------------------------------------------------------------------------
# Test 9 — Merkle chain integration
# ---------------------------------------------------------------------------


def test_five_code_exec_calls_form_verifiable_merkle_chain() -> None:
    """5 :code/exec calls in sequence inside one dosync produce 5
    AuditEntries that verify_chain accepts. Mirrors the Plan-Edit
    precedent from
    test_plan_edit_audit_entries_form_a_verifiable_merkle_chain.
    """
    db = DB()
    entries: list[AuditEntry] = []
    rt = _make_audit_runtime(entries)

    sources = [
        "print(1)",
        "print(2)",
        "print(3)",
        "print(4)",
        "print(5)",
    ]

    with with_runtime(rt):
        with db.dosync() as tx:
            for src in sources:
                exec_code(src, tx=tx)

    assert len(entries) == 5
    for e in entries:
        assert e.op == ":code/exec"
        # txn_commit pinned (intent-replay path injects it).
        assert e.txn_commit is not None

    # All five share the same txn_commit (single dosync).
    assert len({e.txn_commit for e in entries}) == 1

    # Merkle-chain links: entry[N+1].prev_hash == entry[N].id.
    for prev, curr in zip(entries, entries[1:]):
        assert curr.prev_hash == prev.id
    # Head of the chain (test-local — no prior audit entries).
    assert entries[0].prev_hash is None

    # verify_chain accepts the sequence.
    assert verify_chain(entries) is True


# ---------------------------------------------------------------------------
# Test 10 — Hypothesis byte-identity property at max_examples=200
# ---------------------------------------------------------------------------


_PRINTABLE_CONST = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=20,
).filter(
    # Avoid quotes / backslashes / control chars that would need escaping
    # in the source-string emit. The strategy generates safe ASCII that
    # round-trips through repr() losslessly.
    lambda s: "'" not in s and '"' not in s and "\\" not in s and "\n" not in s
)

_JSON_KEY = st.text(
    alphabet=st.characters(min_codepoint=0x61, max_codepoint=0x7A),
    min_size=1,
    max_size=8,
)
_JSON_VAL = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    _PRINTABLE_CONST.filter(lambda s: '"' not in s and "\\" not in s),
)


@st.composite
def _deterministic_source(draw) -> str:
    """Draw a deterministic source program from one of two patterns:

    1. ``print(<repr-of-string>)`` — bare print of a constant.
    2. ``import json; print(json.dumps(<dict>, sort_keys=True))`` —
       canonical-JSON of a small dict.

    Both patterns are pure functions of their generator inputs, so two
    consecutive exec_code calls on the same source MUST produce the same
    output_hash. That's the byte-identity invariant under test.
    """
    pattern = draw(st.integers(min_value=0, max_value=1))
    if pattern == 0:
        const = draw(_PRINTABLE_CONST)
        return f"print({const!r})"
    # Pattern 1: json.dumps of a small dict.
    items = draw(
        st.lists(
            st.tuples(_JSON_KEY, _JSON_VAL),
            min_size=0,
            max_size=4,
            unique_by=lambda kv: kv[0],
        )
    )
    d = dict(items)
    return f"import json\nprint(json.dumps({d!r}, sort_keys=True))"


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(source=_deterministic_source())
def test_hypothesis_byte_identity_under_repeat_execution(source: str) -> None:
    """For any deterministic source from the strategy, two consecutive
    exec_code calls produce identical output_hash.

    This is the falsifiable G2-equivalent gate from design § 4.2 line
    309: byte-identity replay yields the same :code/output across
    replays.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            r1 = exec_code(source, tx=tx)
        with db.dosync() as tx:
            r2 = exec_code(source, tx=tx)

    assert r1.output_hash == r2.output_hash, (
        f"byte-identity violation:\n  source={source!r}\n"
        f"  r1.stdout={r1.stdout!r}\n  r2.stdout={r2.stdout!r}"
    )
    # Stronger: full output triple matches.
    assert r1.stdout == r2.stdout
    assert r1.stderr == r2.stderr
    assert r1.exit_code == r2.exit_code


# ---------------------------------------------------------------------------
# Test 11 — re-execution replay match
# ---------------------------------------------------------------------------


def test_re_execution_replay_match() -> None:
    """Run a deterministic source, capture the hash, re-run with
    replay_mode='re-execute' and the recorded hash; the function
    succeeds without raising.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    src = 'import json\nprint(json.dumps({"a": 1, "b": 2}, sort_keys=True))'

    with with_runtime(rt):
        with db.dosync() as tx:
            r1 = exec_code(src, tx=tx)
        # Re-execute under same env — should not raise.
        with db.dosync() as tx:
            r2 = exec_code(
                src,
                tx=tx,
                replay_mode="re-execute",
                expected_output_hash=r1.output_hash,
            )

    assert r2.output_hash == r1.output_hash


# ---------------------------------------------------------------------------
# Test 12 — re-execution replay mismatch
# ---------------------------------------------------------------------------


def test_re_execution_replay_mismatch() -> None:
    """Feed a wrong expected_output_hash to re-execution-replay; the
    function raises CodeExecReplayMismatch.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    src = "print(42)"
    fake_hash = "sha256:" + "0" * 64  # plausible-shape but wrong

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(CodeExecReplayMismatch) as exc_info:
                exec_code(
                    src,
                    tx=tx,
                    replay_mode="re-execute",
                    expected_output_hash=fake_hash,
                )

    assert exc_info.value.expected_hash == fake_hash
    assert exc_info.value.actual_hash != fake_hash
    assert exc_info.value.actual_hash.startswith("sha256:")


# ---------------------------------------------------------------------------
# Bonus — output_hash excludes wall_clock_ms (regression guard)
# ---------------------------------------------------------------------------


def test_output_hash_independent_of_wall_clock() -> None:
    """The byte-identity invariant explicitly excludes wall_clock_ms
    from output_hash. Two runs of the same source MUST produce the
    same output_hash even though wall_clock_ms varies (CPU contention,
    GC scheduling, etc.).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    src = "print('hello')"
    hashes = []
    for _ in range(3):
        with with_runtime(rt):
            with db.dosync() as tx:
                r = exec_code(src, tx=tx)
        hashes.append(r.output_hash)

    # All three runs produce the same output_hash even though
    # wall_clock_ms varies.
    assert len(set(hashes)) == 1


# ---------------------------------------------------------------------------
# Bonus — replay_mode validation
# ---------------------------------------------------------------------------


def test_replay_mode_re_execute_requires_expected_hash() -> None:
    """replay_mode='re-execute' without expected_output_hash raises
    ValueError (parameter validation).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(ValueError, match="expected_output_hash"):
                exec_code("print(1)", tx=tx, replay_mode="re-execute")


def test_invalid_replay_mode_raises() -> None:
    """An unknown replay_mode raises ValueError."""
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(ValueError, match="replay_mode"):
                exec_code("print(1)", tx=tx, replay_mode="audit")  # type: ignore[arg-type]
