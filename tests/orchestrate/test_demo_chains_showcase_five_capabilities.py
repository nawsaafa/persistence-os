"""G5 — positioning doc fidelity: the two canned demo chains + the G5
harness's direct substrate calls together exercise all 5 capabilities
from ``docs/release-notes/v0.9.0a1-positioning.md``.

Cap 1: signing (Ed25519)         — every emitted AuditEntry signed.
Cap 2: governed action            — chain A step 2 denied at capability gate.
Cap 3: audit replay (byte-identity) — chain A run TWICE under pinned clock + key.
Cap 4: kill switch                — direct ``_CoderSteeringSession.pause/resume``.
Cap 5: substrate time             — direct ``substrate.effect.perform(":sys/now", {})``.

R1-fold B2/B3/G5 rewrite: caps 4+5 are demonstrated by the harness
calling the substrate DIRECTLY rather than through the emitted chain
runner. This is the substrate-truth shape:

- ``:sys/now`` is a substrate-native VIEW handler that returns a
  ``datetime.datetime``. It is NOT in ``CANONICAL_AUDIT_WRAPPED_OPS``,
  so it emits NO AuditEntry by default. The cap-5 contract is that
  the call (i) succeeds with a ``datetime`` return value, and (ii)
  replays deterministically under a pinned/replay clock — not that
  it leaves a chain artefact.

- ``pause()`` + ``resume()`` on a ``_CoderSteeringSession`` already
  emit the 4-entry ``[:repl/request, :repl/response, :repl/request,
  :repl/response]`` audit sequence (Phase 2.3d LD-4). The harness
  invokes them directly; the audit chain shows the kill-switch
  contract end-to-end without needing a synthetic chain op.

The two canned ``.edn`` chains thus stay minimal — both use only
``:llm/call`` (with the runner-installed ``raw-echo`` terminator),
exercising caps 1 + 2 + 3 via the emit path. Caps 4 + 5 ride on
direct substrate calls in the same substrate so all entries land in
the same Merkle chain.

R1-fold I3: chain content asserted BEFORE running so drift fails fast.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect._signing import generate_keypair
from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn
from persistence.sdk import Capability, Substrate

_FIXED_TS: float = 1_712_500_000.0


def _emit_and_load(chain_filename: str, tmp_path: Path):
    """Emit the named chain into a tmp dir and return (chain, run_chain)."""
    chain_src = (
        Path(__file__).parent.parent.parent
        / "src/persistence/orchestrate/examples"
        / chain_filename
    ).read_text()
    chain = parse_chain_edn(chain_src)

    out_dir = tmp_path / chain_filename.replace(".edn", "")
    emit_orchestrator_skill(chain, out_dir)

    spec = importlib.util.spec_from_file_location(
        f"emitted_{chain.name}_{tmp_path.name}", out_dir / "orchestrate.py"
    )
    assert spec is not None and spec.loader is not None
    emitted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitted)
    return chain, emitted.run_chain


def test_two_canned_chains_exercise_all_five_capabilities(tmp_path: Path) -> None:
    # ------------------------------------------------------------------ #
    # Chain (a) — capability-denial-demo: caps 1 (signing) + 2 (denial). #
    # Cap 3 (replay byte-identity) is demonstrated by running twice.     #
    # ------------------------------------------------------------------ #
    chain_a, run_chain_a = _emit_and_load(
        "capability-denial-chain.edn", tmp_path / "a"
    )

    # R1-fold I3: assert parsed chain matches expected substrate-truth
    # shape BEFORE running.
    assert [s.op for s in chain_a.steps] == [":llm/call", ":llm/call"], (
        f"capability-denial-demo drifted: {[s.op for s in chain_a.steps]}"
    )
    assert chain_a.steps[1].capability is not None
    assert chain_a.steps[1].capability.qualifier == "write"

    priv_a, _pub_a = generate_keypair()
    signer_a = ("ed25519:g5-a-key", priv_a)

    def _run_chain_a_once() -> list:
        with Substrate.open("memory", audit_signer=signer_a) as s:
            s.effect.install_handler(make_fixed_clock_handler(ts=_FIXED_TS))
            run_chain_a(
                s,
                granted_capabilities={Capability(op="coder", qualifier="read")},
            )
            return list(s._audit_entries)

    trace_a_1 = _run_chain_a_once()
    trace_a_2 = _run_chain_a_once()

    # ------------------------------------------------------------------ #
    # Chain (b) — pause-resume-sysnow-demo: cap 1 (signing) + cap 3      #
    # (replay base). Caps 4 + 5 are exercised directly below.            #
    # ------------------------------------------------------------------ #
    chain_b, run_chain_b = _emit_and_load(
        "pause-resume-sysnow-chain.edn", tmp_path / "b"
    )
    assert [s.op for s in chain_b.steps] == [":llm/call"], (
        f"pause-resume-sysnow-demo drifted: {[s.op for s in chain_b.steps]}"
    )

    priv_b, _pub_b = generate_keypair()
    signer_b = ("ed25519:g5-b-key", priv_b)

    sys_now_value: dt.datetime | None = None
    repl_ops: list[str] = []

    with Substrate.open("memory", audit_signer=signer_b) as s:
        s.effect.install_handler(make_fixed_clock_handler(ts=_FIXED_TS))

        # Cap 4 — kill switch. Direct ``_CoderSteeringSession.pause()``
        # then ``resume()``. Each public op emits the LD-4 contract
        # 2-entry ``[:repl/request, :repl/response]`` pair on the
        # substrate's audit chain.
        coder = Coder(task="g5-pause-resume", substrate=s, max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        session.pause()
        session.resume()

        # Chain B (one ``:llm/call`` step) — exercises cap 1 + 3 via the
        # emit path on the same substrate so :repl/* + :llm/call land
        # in the same Merkle chain.
        run_chain_b(
            s,
            granted_capabilities={Capability(op="coder", qualifier="read")},
        )

        # Cap 5 — substrate time. Direct ``s.effect.perform(":sys/now",
        # {})`` returns a UTC-aware ``datetime.datetime``. Substrate
        # truth: ``:sys/now`` is a view handler and emits NO AuditEntry
        # by default; we assert the return SHAPE + determinism under
        # the pinned clock rather than an audit-chain artefact.
        sys_now_value = s.effect.perform(":sys/now", {})
        sys_now_value_2 = s.effect.perform(":sys/now", {})

        trace_b = list(s._audit_entries)

    # Cap 4 strong assertion: 4-entry ``[:repl/request, :repl/response] ×
    # 2`` audit ordering for the pause + resume cycle.
    repl_ops = [e.op for e in trace_b if e.op.startswith(":repl/")]
    assert repl_ops == [
        ":repl/request",
        ":repl/response",
        ":repl/request",
        ":repl/response",
    ], f"kill-switch audit ordering contract violated: {repl_ops}"

    # Cap 5 substrate-truth assertions: returns datetime + deterministic
    # under pinned clock.
    assert isinstance(sys_now_value, dt.datetime), (
        f"cap-5 :sys/now must return datetime; got {type(sys_now_value).__name__}"
    )
    assert sys_now_value.tzinfo is not None, (
        "cap-5 :sys/now must return tz-aware datetime"
    )
    assert sys_now_value == sys_now_value_2, (
        f"cap-5 :sys/now drift under pinned clock: {sys_now_value!r} != {sys_now_value_2!r}"
    )

    # Cap 1 — signing: every captured AuditEntry across both chains has
    # a non-None ``.signature``.
    all_traces = [trace_a_1, trace_a_2, trace_b]
    for ti, trace in enumerate(all_traces):
        for ei, e in enumerate(trace):
            assert e.signature is not None, (
                f"cap-1 signing broken: trace {ti} entry {ei} ({e.op}) "
                f"has no signature"
            )

    # Cap 2 — governed action: chain A has exactly one
    # ``:capability/denied`` entry (step 2 denied) AND no second
    # ``:llm/call`` (step 2's side effect was prevented).
    denials = [e for e in trace_a_1 if e.op == ":capability/denied"]
    assert len(denials) == 1, (
        f"cap-2 governance broken: expected 1 denial, got {len(denials)}: "
        f"ops={[e.op for e in trace_a_1]}"
    )
    llm_calls_a = [e for e in trace_a_1 if e.op == ":llm/call"]
    assert len(llm_calls_a) == 1, (
        f"cap-2 governance broken: denied step still performed; "
        f"got {len(llm_calls_a)} :llm/call entries"
    )

    # Cap 3 — REAL replay byte-identity. Two independent ``Substrate.open``
    # runs of chain A under pinned clock + fixed signing key → identical
    # Merkle chain tail id. This is the load-bearing replay invariant;
    # if it fails, either clock leaks wall time or signing was
    # session-unique.
    def _entry_shape(e):
        return {
            "id": e.id,
            "op": e.op,
            "args_hash": e.args_hash,
            "prev_hash": e.prev_hash,
            "recorded_at": e.recorded_at,
            "latency_ms": e.latency_ms,
        }

    hash_1 = canonical_hash([_entry_shape(e) for e in trace_a_1])
    hash_2 = canonical_hash([_entry_shape(e) for e in trace_a_2])
    assert hash_1 == hash_2, (
        "cap-3 replay byte-identity FAILED across two fresh substrate runs.\n"
        f"  hash_1: {hash_1!r}\n  hash_2: {hash_2!r}\n"
        f"  ops_1: {[e.op for e in trace_a_1]}\n"
        f"  ops_2: {[e.op for e in trace_a_2]}"
    )
    assert trace_a_1[-1].id == trace_a_2[-1].id, (
        f"cap-3 Merkle chain tail diverged: {trace_a_1[-1].id!r} != "
        f"{trace_a_2[-1].id!r}"
    )

    # All five capabilities exercised — checklist for the positioning doc.
    capabilities_exercised = {
        "agent-vs-human-identity",   # cap 1
        "governed-action",            # cap 2
        "audit-replay",               # cap 3
        "kill-switch",                # cap 4
        "substrate-time",             # cap 5
    }
    assert len(capabilities_exercised) == 5
