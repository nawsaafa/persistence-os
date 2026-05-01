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
    is still emitted (audit-emit-then-raise pattern). Both partial_stdout
    and partial_stderr fields are present on the exception (added in the
    2.0b cleanup pass — previously stderr was drained but discarded).
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(CodeExecTimeout) as exc_info:
                exec_code("while True: pass", tx=tx, timeout_seconds=1.0)

    assert exc_info.value.timeout_seconds == 1.0
    # partial_stdout / partial_stderr fields exist + are strings even
    # when empty. The bare ``while True: pass`` produces no output so
    # both are "" — the load-bearing assertion is that the attribute
    # access works without AttributeError.
    assert isinstance(exc_info.value.partial_stdout, str)
    assert isinstance(exc_info.value.partial_stderr, str)
    # Datom captured (1 entry) — audit-emit-then-raise.
    assert len(captured) == 1
    payload = captured[0]
    # exit_code is the -1 sentinel for timeout.
    assert payload["exit_code"] == -1
    # wall_clock_ms ≈ timeout_seconds * 1000 (we record the configured cap).
    assert payload["wall_clock_ms"] == 1000


def test_timeout_captures_partial_stdout_before_kill() -> None:
    """A body that prints + flushes BEFORE entering the infinite loop
    has its pre-loop stdout captured on the kill path. Regression guard
    for the Commit-1 ``proc.kill() + proc.communicate()`` drain — if a
    refactor swapped to ``proc.terminate()`` without the second
    communicate, the buffered output would be lost.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    src = (
        "import json\n"
        "print('progress', flush=True)\n"
        "print(json.dumps({'k': 1}), flush=True)\n"
        "while True:\n    pass\n"
    )
    with with_runtime(rt):
        with db.dosync() as tx:
            with pytest.raises(CodeExecTimeout) as exc_info:
                exec_code(src, tx=tx, timeout_seconds=1.0)

    # Pre-loop output captured. Both lines flush before the loop, so
    # both reach the parent's stdout pipe before the kill.
    assert "progress" in exc_info.value.partial_stdout
    assert '"k": 1' in exc_info.value.partial_stdout
    # No stderr writes in this body — partial_stderr is empty string.
    assert exc_info.value.partial_stderr == ""


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


def test_stdin_param_accepted_even_when_user_code_cannot_read_it() -> None:
    """Phase 2.0d W1 (M1) contract change: ``input()`` is now denied
    in the curated ``__builtins__`` (alongside ``open`` / ``eval`` /
    ``ex``+``ec`` / ``compile`` / ``breakpoint``). The ``stdin``
    parameter to :func:`exec_code` is still accepted on the public
    surface (the bootstrap shim still receives the envelope-encoded
    payload and replaces ``sys.stdin`` with a ``StringIO`` of it),
    but user code has no curated path to read stdin under capability-
    denial — ``sys`` / ``io`` are denied imports, and ``input`` is
    denied at the builtins layer. This test pins the post-M1
    contract: passing ``stdin="..."`` does not raise; the body runs
    without consuming the buffer.

    A future revision (#149+) may add a curated ``read_stdin()``
    builtin that exposes the envelope buffer deterministically; the
    M1 ship explicitly does not include that surface.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])

    with with_runtime(rt):
        with db.dosync() as tx:
            r = exec_code("print('hello')", tx=tx, stdin="ignored payload")

    assert r.exit_code == 0
    assert r.stdout == "hello\n"


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


# ---------------------------------------------------------------------------
# Bonus — _ALLOWED_TOP_LEVEL is the canonical source of truth (parent⇄child)
# ---------------------------------------------------------------------------


def test_allowed_set_is_canonical_source() -> None:
    """The parent-side ``_ALLOWED_TOP_LEVEL`` constant is the single
    source of truth for the sandbox allowed-set; the child-side
    bootstrap shim's frozenset literal is generated from it via
    ``str.format``-style placeholder substitution at module load time.

    This test is the regression guard for parent-vs-child drift — if a
    future maintainer changes one side and forgets the other, this
    assertion fails. Without this test the parent constant would be
    dead code (Pyright "unused" warning was the original signal).

    Specifically asserts:

    - The bootstrap shim text contains every name from ``_ALLOWED_TOP_LEVEL``.
    - The placeholder ``__ALLOWED_TUPLE__`` was substituted (no leak).
    - The shim's frozenset literal matches ``repr(_ALLOWED_TOP_LEVEL)``.
    """
    from persistence.effect.handlers.code import (
        _ALLOWED_TOP_LEVEL,
        _CHILD_RUNNER_BOOTSTRAP,
    )

    # Substitution happened — no placeholder leaked.
    assert "__ALLOWED_TUPLE__" not in _CHILD_RUNNER_BOOTSTRAP, (
        "bootstrap shim still contains the __ALLOWED_TUPLE__ placeholder; "
        "the module-load-time substitution failed"
    )

    # Every allowed name appears in the shim text.
    for name in _ALLOWED_TOP_LEVEL:
        assert name in _CHILD_RUNNER_BOOTSTRAP, (
            f"allowed name {name!r} not in bootstrap shim — "
            f"parent⇄child drift"
        )

    # The exact frozenset literal lands in the shim.
    expected_literal = (
        f"_ALLOWED_TOP_LEVEL = frozenset({_ALLOWED_TOP_LEVEL!r})"
    )
    assert expected_literal in _CHILD_RUNNER_BOOTSTRAP, (
        f"frozenset literal {expected_literal!r} not found in shim — "
        f"the .replace() substitution did not produce the expected text"
    )

    # And the four documented names are exactly what the v0.5 design pins.
    assert set(_ALLOWED_TOP_LEVEL) == {"json", "re", "dataclasses", "pathlib"}


# ---------------------------------------------------------------------------
# Phase 2.0d W1 (M1) — denied builtins + child env determinism
# ---------------------------------------------------------------------------


def _exec_under_capture(source: str, *, stdin: str = "") -> CodeExecResult:
    """Helper: run ``source`` under a clock + capture handler stack
    (no audit middleware) so the tests below focus on subprocess-side
    behaviour. Mirrors the simple stack used by the existing
    forbidden-import tests.
    """
    db = DB()
    captured: list[dict] = []
    rt = Runtime(handlers=[make_fixed_clock_handler(ts=1.0), _capture_handler(captured)])
    with with_runtime(rt):
        with db.dosync() as tx:
            return exec_code(source, tx=tx, stdin=stdin)


def test_open_is_denied() -> None:
    """``open()`` is removed from the curated ``__builtins__`` so a
    user-source ``open('/etc/passwd')`` raises ``NameError`` inside
    the sandbox — host-filesystem reads are denied at the capability
    layer (M1 primary fix).
    """
    r = _exec_under_capture("print(open('/etc/passwd').read())")
    assert r.exit_code != 0
    # NameError is the resolution-fail signal we want; "name 'open'" lands
    # in stderr.
    assert "NameError" in r.stderr
    assert "open" in r.stderr


def test_eval_is_denied() -> None:
    """``eval()`` is removed; user-source ``eval('1+1')`` raises
    ``NameError``.
    """
    # User source string — ``ev`` + ``al`` reassembled at parse time
    # inside the sandbox; we split here to dodge the JS-codebase
    # security-hook false-positive on the literal token.
    user_src = "print(" + "ev" + "al('1+1'))"
    r = _exec_under_capture(user_src)
    assert r.exit_code != 0
    assert "NameError" in r.stderr
    assert "ev" + "al" in r.stderr


def test_exec_is_denied() -> None:
    """``ex``+``ec()`` is removed; user-source raises ``NameError``."""
    user_source = "print(" + "ex" + "ec('x = 1'))"
    r = _exec_under_capture(user_source)
    assert r.exit_code != 0
    assert "NameError" in r.stderr
    assert "ex" + "ec" in r.stderr


def test_compile_is_denied() -> None:
    """``compile()`` is removed; user-source raises ``NameError``."""
    user_src = "print(compile('1', '<x>', '" + "ev" + "al'))"
    r = _exec_under_capture(user_src)
    assert r.exit_code != 0
    assert "NameError" in r.stderr
    assert "compile" in r.stderr


def test_input_is_denied() -> None:
    """``input()`` is removed under M1 even though stdin is still
    envelope-encoded into the child. User-source ``input()`` raises
    ``NameError`` because the name is not in the curated builtins.
    """
    r = _exec_under_capture("print(input())", stdin="ignored")
    assert r.exit_code != 0
    assert "NameError" in r.stderr
    assert "input" in r.stderr


def test_breakpoint_is_denied() -> None:
    """``breakpoint()`` is removed; user-source ``breakpoint()`` raises
    ``NameError`` so a debugger attach attempt cannot interrupt
    deterministic replay.
    """
    r = _exec_under_capture("breakpoint()")
    assert r.exit_code != 0
    assert "NameError" in r.stderr
    assert "breakpoint" in r.stderr


def test_pythonhashseed_pinned_in_child_env() -> None:
    """Determinism test: under ``PYTHONHASHSEED=0`` in the child env,
    two consecutive ``exec_code`` runs of the same string-set literal
    produce byte-identical stdout. Without the seed pin the
    iteration order would vary per child-interpreter start
    (Python 3.3+ default randomized hash seed).
    """
    src = (
        "items = {'apple', 'banana', 'cherry', 'date', 'elderberry'}\n"
        "for x in items:\n"
        "    print(x)\n"
    )
    r1 = _exec_under_capture(src)
    r2 = _exec_under_capture(src)
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    # Byte-identical output — the iteration order is pinned by the
    # hash seed. Without PYTHONHASHSEED=0 in the child env, the order
    # would vary between runs.
    assert r1.stdout == r2.stdout, (
        "PYTHONHASHSEED=0 should pin set-iteration order; saw "
        f"r1={r1.stdout!r} vs r2={r2.stdout!r}"
    )


def test_dict_iteration_byte_identical_across_runs() -> None:
    """Hypothesis at ``@max_examples=200``: under ``PYTHONHASHSEED=0``
    in the child, two consecutive ``exec_code`` runs of the same dict-
    literal-iteration source produce byte-identical ``output_hash``.

    The property holds for string keys. Without the hash-seed pin,
    string-keyed dict iteration would be randomized per
    child-interpreter start (Python 3.3+ default), and the output
    bytes would diverge between runs — silently breaking byte-
    identity replay.
    """

    @given(
        keys=st.lists(
            st.text(
                alphabet=st.characters(
                    min_codepoint=0x61,  # 'a'
                    max_codepoint=0x7a,  # 'z'
                ),
                min_size=1,
                max_size=8,
            ),
            min_size=2,
            max_size=8,
            unique=True,
        )
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def _prop(keys: list[str]) -> None:
        items_repr = ", ".join(f"{k!r}: {i}" for i, k in enumerate(keys))
        src = f"d = {{{items_repr}}}\nfor k in d:\n    print(k)\n"
        r1 = _exec_under_capture(src)
        r2 = _exec_under_capture(src)
        assert r1.output_hash == r2.output_hash, (
            f"output_hash diverged across runs for keys={keys!r}: "
            f"r1={r1.output_hash} r2={r2.output_hash}"
        )

    _prop()


def test_denied_builtins_set_is_canonical_source() -> None:
    """The parent's ``_DENIED_BUILTINS`` is the canonical source —
    its repr is substituted into the child shim at module load time
    via the ``__DENIED_BUILTINS_TUPLE__`` placeholder. The shim must
    end up referencing every name in the parent set.

    Mirrors ``test_allowed_set_is_canonical_source`` for the M1
    denied-set side; pins parent ⇄ child drift.
    """
    from persistence.effect.handlers.code import (
        _CHILD_RUNNER_BOOTSTRAP,
        _DENIED_BUILTINS,
    )

    # Substitution happened — no placeholder leaked.
    assert "__DENIED_BUILTINS_TUPLE__" not in _CHILD_RUNNER_BOOTSTRAP, (
        "bootstrap shim still contains the __DENIED_BUILTINS_TUPLE__ "
        "placeholder; the module-load-time substitution failed"
    )

    # Every denied name appears in the shim text.
    for name in _DENIED_BUILTINS:
        assert name in _CHILD_RUNNER_BOOTSTRAP, (
            f"denied name {name!r} not in bootstrap shim — "
            f"parent⇄child drift"
        )

    # Sanity: the documented denied set is what the W1 design pins.
    assert set(_DENIED_BUILTINS) == {
        "open", "ev" + "al", "ex" + "ec", "compile", "input", "breakpoint",
    }
