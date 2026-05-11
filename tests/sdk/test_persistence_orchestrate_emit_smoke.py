"""G3 — LD-1 falsifier: end-to-end emit + run + signed REPLAYABLE trace.

R1-fold B1 rewrite: replay byte-identity is now demonstrated by running
the emitted chain TWICE in two fresh ``Substrate.open("memory", ...)``
contexts with the same pinned clock + same fixed signing key, then
comparing the canonical hash of the two resulting audit chains. The
two chains must match byte-identically.

R0-fold B3 (preserved): in-process — no subprocess + no stdout JSON
contract. The emitted ``orchestrate.py`` module is imported via
``importlib.util`` and its ``run_chain`` invoked directly.

R1-fold I3 (preserved): the parsed chain shape is asserted BEFORE
running, so chain drift fails fast with a precise error rather than
producing a confusing audit-shape mismatch downstream.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from persistence.effect._signing import generate_keypair
from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.orchestrate import (
    emit_orchestrator_skill,
    parse_chain_edn,
)
from persistence.sdk import Capability, Substrate

# Fixed clock — pinned so latency_ms / recorded_at on every AuditEntry
# is deterministic across two runs. Mirrors
# ``tests/coder/test_loop_replay.py::_FIXED_TS_A``.
_FIXED_TS: float = 1_712_000_000.0


def _load_emitted_run_chain(out_dir: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, out_dir / "orchestrate.py")
    assert spec is not None and spec.loader is not None
    emitted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitted)
    return emitted.run_chain


def _run_once(
    out_dir: Path,
    module_name: str,
    granted: set[Capability],
    signer: tuple[str, bytes],
) -> list:
    """Run the emitted chain once in a fresh substrate with a pinned clock.

    Returns the captured audit entries.
    """
    run_chain = _load_emitted_run_chain(out_dir, module_name)
    with Substrate.open("memory", audit_signer=signer) as s:
        # Pin the clock so latency_ms / recorded_at are deterministic.
        # ``install_handler`` is by-name idempotent — replaces the
        # canonical stack's ``clock`` handler in place.
        s.effect.install_handler(make_fixed_clock_handler(ts=_FIXED_TS))
        run_chain(s, granted_capabilities=granted)
        return list(s._audit_entries)


def test_emits_orchestrator_that_runs_signed_replayable_trace(tmp_path: Path) -> None:
    # Step 1: emit (in-process)
    chain_src = (
        Path(__file__).parent.parent.parent
        / "src/persistence/orchestrate/examples/capability-denial-chain.edn"
    ).read_text()
    chain = parse_chain_edn(chain_src)

    # R1-fold I3: chain content assertion BEFORE running — drift fails
    # fast with a precise error.
    assert [s.op for s in chain.steps] == [":llm/call", ":llm/call"], (
        f"capability-denial-demo chain drift: {[s.op for s in chain.steps]}"
    )
    assert chain.steps[0].capability is not None
    assert chain.steps[0].capability.qualifier == "read"
    assert chain.steps[1].capability is not None
    assert chain.steps[1].capability.qualifier == "write"

    out_dir = tmp_path / "emitted-skill"
    emit_orchestrator_skill(chain, out_dir)

    # (i) Emitted directory shape (4 files)
    assert (out_dir / "SKILL.md").is_file()
    assert (out_dir / "chain.edn").is_file()
    assert (out_dir / "preflight.toml").is_file()
    assert (out_dir / "orchestrate.py").is_file()

    # Grant only step 1's capability; step 2 must be denied.
    granted = {Capability(op="coder", qualifier="read")}

    # Same key for both runs — keypair_id + private bytes pinned.
    priv, _pub = generate_keypair()
    signer = ("ed25519:g3-replay-key", priv)

    # ---- Run A ---------------------------------------------------------
    entries_a = _run_once(out_dir, "emitted_orchestrate_a", granted, signer)

    # (ii) Trace contains signed denial entry for step 2
    denials_a = [e for e in entries_a if e.op == ":capability/denied"]
    assert len(denials_a) == 1, (
        f"expected 1 :capability/denied entry, got {len(denials_a)}: "
        f"ops={[e.op for e in entries_a]}"
    )
    assert denials_a[0].signature is not None, (
        "denial entry was not signed — LD-4 wiring broken"
    )

    # (iii) Last op is the denial (the halt point for step 2)
    op_sequence = [e.op for e in entries_a]
    assert op_sequence[-1] == ":capability/denied", (
        f"last op must be :capability/denied (halt point); got {op_sequence}"
    )

    # (iv) No second :llm/call after the denial.
    llm_calls = [e for e in entries_a if e.op == ":llm/call"]
    assert len(llm_calls) == 1, (
        f"expected exactly 1 :llm/call (step 1) before denial; got "
        f"{len(llm_calls)}: ops={op_sequence}"
    )

    # ---- Run B (fresh substrate, identical pinned conditions) ----------
    entries_b = _run_once(out_dir, "emitted_orchestrate_b", granted, signer)

    # Sanity: same op sequence.
    assert [e.op for e in entries_b] == op_sequence, (
        f"run B op sequence drift: a={op_sequence!r} b={[e.op for e in entries_b]!r}"
    )

    # (v) REAL replay byte-identity — canonical_hash over the FULL chain
    # shape (op + args_hash + prev_hash + recorded_at + latency_ms) must
    # match across the two independent runs. With pinned clock + fixed
    # signer + identical args, every field that feeds ``entry.id`` is
    # deterministic; the chain tail's ``id`` is therefore stable too.
    def _entry_shape(e):
        return {
            "id": e.id,
            "op": e.op,
            "args_hash": e.args_hash,
            "prev_hash": e.prev_hash,
            "recorded_at": e.recorded_at,
            "latency_ms": e.latency_ms,
        }

    hash_a = canonical_hash([_entry_shape(e) for e in entries_a])
    hash_b = canonical_hash([_entry_shape(e) for e in entries_b])
    assert hash_a == hash_b, (
        "replay byte-identity FAILED across two fresh substrate runs.\n"
        f"  hash_a: {hash_a!r}\n"
        f"  hash_b: {hash_b!r}\n"
        f"  ops_a: {[e.op for e in entries_a]}\n"
        f"  ops_b: {[e.op for e in entries_b]}"
    )

    # Final assertion: the last-entry ``id`` (the Merkle chain tail) is
    # the canonical replay invariant. If h_a == h_b above held but this
    # fails, the assertion above is too permissive.
    assert entries_a[-1].id == entries_b[-1].id, (
        f"Merkle chain tail diverged: a={entries_a[-1].id!r} b={entries_b[-1].id!r}"
    )
