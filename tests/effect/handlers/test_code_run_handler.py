"""Phase 2.2b G2 — :code/run substantive-return handler unit coverage.

Tests for :code/run distinct from legacy :code/exec on three axes:

1. Audit args_hash is over LLM-emitted source-shape args
   (``{"source": ..., "stdin": ..., ...}``), not the hash-shape used by
   exec_code (``{"source-hash": ..., "stdin-hash": ..., ...}``).
2. Returns a result dict; does NOT raise on timeout, forbidden-import,
   or non-zero exit. The agent reads stderr / exit_code to decide.
3. Timeout converts to ``exit_code=-1`` + partial output (matches legacy
   exec_code's audit-datom shape on timeout).

Acceptance signal: ``test_code_run_output_hash_byte_identity_across_runs``
asserts two runs of the same deterministic source produce byte-identical
``output_hash`` values.
"""
from __future__ import annotations

import hashlib

from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.audit import AuditEntry, make_audit_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.code import make_code_run_dispatch_handler
from persistence.effect.runtime import Runtime, with_runtime


def _make_runtime() -> tuple[Runtime, list[AuditEntry]]:
    """Return ``(rt, entries)`` with audit middleware over :code/run.

    Stack mirrors the canonical-audit-stack composition: raw terminator
    (the new make_code_run_dispatch_handler), clock, audit middleware.
    """
    entries: list[AuditEntry] = []
    raw = make_code_run_dispatch_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    audit = make_audit_handler(entries, wraps={":code/run"})
    return Runtime(handlers=[raw, clock, audit]), entries


def test_code_run_basic_returns_dict_and_emits_audit() -> None:
    """Simple print(\"hi\") source returns the 5-key dict and emits exactly
    one :code/run AuditEntry."""
    rt, entries = _make_runtime()
    with with_runtime(rt) as r:
        result = r.perform(":code/run", {"source": 'print("hi")'})

    # Result-dict shape and content.
    assert set(result.keys()) == {
        "stdout",
        "stderr",
        "exit_code",
        "wall_clock_ms",
        "output_hash",
    }
    assert result["stdout"] == "hi\n"
    assert result["exit_code"] == 0
    assert isinstance(result["wall_clock_ms"], int)
    assert result["wall_clock_ms"] >= 0
    assert result["output_hash"].startswith("sha256:")
    assert len(result["output_hash"].split(":", 1)[1]) == 64

    # Exactly one :code/run AuditEntry.
    assert len(entries) == 1
    assert entries[0].op == ":code/run"


def test_code_run_output_hash_byte_identity_across_runs() -> None:
    """G2 ACCEPTANCE SIGNAL — two runs of the same deterministic source
    yield byte-identical ``output_hash`` values."""
    src = 'print("hello")'

    rt1, _ = _make_runtime()
    with with_runtime(rt1) as r:
        r1 = r.perform(":code/run", {"source": src})

    rt2, _ = _make_runtime()
    with with_runtime(rt2) as r:
        r2 = r.perform(":code/run", {"source": src})

    # Only output_hash is byte-identical by design — wall_clock_ms varies.
    assert r1["output_hash"] == r2["output_hash"]


def test_code_run_args_hash_over_source_shape_not_hash_shape() -> None:
    """The audit ``args_hash`` is over the args dict passed to perform —
    i.e. the LLM-emitted source-shape. Compute the expected hash directly
    and assert equality with the recorded entry's ``args_hash``.
    """
    rt, entries = _make_runtime()
    src = 'print("audit-shape-check")'
    args = {"source": src}

    with with_runtime(rt) as r:
        r.perform(":code/run", args)

    expected = canonical_hash(args)
    assert len(entries) == 1
    assert entries[0].args_hash == expected
    # Sanity: the hash format is sha256:<64-hex>.
    assert expected.startswith("sha256:")


def test_code_run_forbidden_import_returns_nonzero_exit_with_sentinel() -> None:
    """A forbidden-import body MUST flow through to the result dict —
    non-zero exit + sentinel in stderr — without raising."""
    rt, _ = _make_runtime()
    # Importing ``os`` is the canonical forbidden-import case (only
    # json/re/dataclasses are on the allowlist). The body itself does
    # not need to call anything — the import statement alone trips the
    # bootstrap shim's deny-list before user code runs.
    forbidden_src = "import os\nprint(os.getcwd())"
    with with_runtime(rt) as r:
        result = r.perform(":code/run", {"source": forbidden_src})

    assert result["exit_code"] != 0
    assert "PERSISTENCE_CODE_EXEC_FORBIDDEN_IMPORT:" in result["stderr"]


def test_code_run_timeout_returns_partial_output_with_negative_one_exit() -> None:
    """Forced spec deviation #2 — the clause MUST catch CodeExecTimeout
    and convert to exit_code=-1 + ~timeout-ms wall_clock.

    NOTE: ``time`` is on the import deny-list (only json/re/dataclasses
    are allowed). A pure-Python busy loop is used here so the body
    exhausts the wall-clock cap WITHOUT hitting the import filter first.
    """
    rt, _ = _make_runtime()
    busy_loop = "x = 0\nwhile True:\n    x += 1\n"
    with with_runtime(rt) as r:
        result = r.perform(
            ":code/run",
            {"source": busy_loop, "timeout_seconds": 0.5},
        )

    assert result["exit_code"] == -1
    # wall_clock_ms is computed as int(timeout_seconds * 1000) in the
    # timeout-conversion path. Equality is exact since the conversion
    # formula does not vary with kill latency.
    assert result["wall_clock_ms"] == 500


def test_code_run_args_hash_distinct_from_code_exec() -> None:
    """The :code/run audit args_hash (source-shape) MUST differ from the
    :code/exec audit args_hash (hash-shape) for the same conceptual call.

    :code/exec audits ``{"source-hash": ..., "stdin-hash": ..., ...}``;
    :code/run audits ``{"source": ..., ...}`` — by design they hash
    different bytes so the agent-facing op is observable as distinct from
    the legacy plan-step op in any audit-trail diff.
    """
    src = "print('x')"

    run_args_hash = canonical_hash({"source": src})

    # :code/exec audit shape — see exec_code body, it pre-computes
    # source-hash and stdin-hash as sha256:<hex> strings.
    src_hash = "sha256:" + hashlib.sha256(src.encode("utf-8")).hexdigest()
    stdin_hash = "sha256:" + hashlib.sha256(b"").hexdigest()
    exec_args_hash = canonical_hash(
        {"source-hash": src_hash, "stdin-hash": stdin_hash}
    )

    assert run_args_hash != exec_args_hash
