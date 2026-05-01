"""``s.txn.fork`` SDK surface unit tests — Phase 2.0c-extended #145ext.

The curated SDK surface ``s.txn.fork`` is a thin pass-through to
:meth:`persistence.fact.DB.fork`. Tests here exercise the surface
binding (stability metadata, namespace exposure, identity stability)
and a handful of integration cases to confirm the pass-through forwards
arguments cleanly. Comprehensive primitive-level coverage lives in
``test_fork.py``; comprehensive audit-shape coverage in
``test_fork_audit.py``.
"""
from __future__ import annotations

import pytest

from persistence.fact import (
    ForkBranchResult,
    ForkChooseError,
    ForkOutsideDosync,
    ForkResult,
)
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_fn(state, item):
    return state + item


def _argmax(branches):
    return max(range(len(branches)), key=lambda i: branches[i].branch_state)


# ---------------------------------------------------------------------------
# 1. Surface binding — pass-through to DB.fork works
# ---------------------------------------------------------------------------


def test_s_txn_fork_happy_path_pass_through():
    """s.txn.fork(...) returns ForkResult; chosen_state matches DB.fork
    contract.
    """
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fork(
                items=[1, 5, 3],
                fn=_add_fn,
                choose=_argmax,
                seed=0,
                tx=tx,
            )
    assert isinstance(result, ForkResult)
    assert result.chosen_index == 1
    assert result.chosen_state == 5
    assert len(result.all_branches) == 3
    for b in result.all_branches:
        assert isinstance(b, ForkBranchResult)


def test_s_txn_fork_branch_state_is_opaque():
    """fn returns dict; choose reads from the dict — confirms the
    pass-through preserves the opaque-state contract.
    """
    def make_dict(state, item):
        return {"item": item, "score": item * 10}

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fork(
                items=[1, 2, 3],
                fn=make_dict,
                choose=lambda b: max(
                    range(len(b)), key=lambda i: b[i].branch_state["score"]
                ),
                tx=tx,
            )
    assert result.chosen_index == 2
    assert result.chosen_state == {"item": 3, "score": 30}


# ---------------------------------------------------------------------------
# 2. Stability metadata — @experimental("v0.8.5a1") per ADR-7
# ---------------------------------------------------------------------------


def test_s_txn_fork_is_marked_experimental():
    """s.txn.fork carries @experimental metadata for the spec gen."""
    with Substrate.open("memory") as s:
        method = s.txn.fork
        underlying = getattr(method, "__func__", method)
        metadata = getattr(underlying, "__sdk_stability__", None)
        assert metadata is not None
        assert metadata.get("level") == "experimental"
        reason = metadata.get("reason") or ""
        assert "Phase 2.0c-extended" in reason or "#145ext" in reason


# ---------------------------------------------------------------------------
# 3. Outside dosync gate
# ---------------------------------------------------------------------------


def test_s_txn_fork_outside_dosync_raises():
    """Calling s.txn.fork outside dosync -> ForkOutsideDosync."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkOutsideDosync, match="dosync"):
            s.txn.fork(
                items=[1, 2],
                fn=_add_fn,
                choose=_argmax,
                seed=0,
            )


def test_s_txn_fork_dosync_without_tx_raises():
    """Inside dosync but tx=None -> ForkOutsideDosync."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkOutsideDosync, match="dosync"):
            with s.txn.dosync():
                s.txn.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                )


# ---------------------------------------------------------------------------
# 4. choose-callback validation routes through SDK
# ---------------------------------------------------------------------------


def test_s_txn_fork_choose_out_of_range_raises():
    """choose returns 99 -> ForkChooseError(ValueError) via the SDK
    pass-through.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fork(
                    items=[1, 2, 3],
                    fn=_add_fn,
                    choose=lambda b: 99,
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_s_txn_fork_choose_returns_bool_raises():
    """choose returns True -> ForkChooseError(TypeError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=lambda b: True,  # type: ignore[return-value]
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


# ---------------------------------------------------------------------------
# 5. on_error pass-through
# ---------------------------------------------------------------------------


def test_s_txn_fork_on_error_continue_pass_through():
    """on_error='continue' -> failed branches recorded with score=None
    + error populated.
    """
    def raising_fn(state, item):
        if item == 99:
            raise RuntimeError("boom")
        return state + item

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fork(
                items=[1, 99, 3],
                fn=raising_fn,
                choose=lambda b: 0,
                seed=0,
                tx=tx,
                on_error="continue",
            )
    assert len(result.all_branches) == 3
    failed = result.all_branches[1]
    assert failed.error is not None
    assert "RuntimeError" in failed.error


def test_s_txn_fork_on_error_stop_propagates():
    """on_error='stop' (default) -> exception propagates."""
    def raising_fn(state, item):
        raise RuntimeError("boom")

    with Substrate.open("memory") as s:
        with pytest.raises(RuntimeError, match="boom"):
            with s.txn.dosync() as tx:
                s.txn.fork(
                    items=[1],
                    fn=raising_fn,
                    choose=lambda b: 0,
                    seed=0,
                    tx=tx,
                    on_error="stop",
                )


# ---------------------------------------------------------------------------
# 6. fork bare layer commits NO substrate facts (pass-through preserves)
# ---------------------------------------------------------------------------


def test_s_txn_fork_bare_layer_commits_no_substrate_facts():
    """The SDK pass-through preserves the bare-layer rollback contract:
    s.txn.fork emits only audit datoms, no substrate facts.
    """
    def stateful_fn(state, item):
        return {"derived": item * 100}

    with Substrate.open("memory") as s:
        pre = list(s.escape.fact.store.all_datoms())

        with s.txn.dosync() as tx:
            s.txn.fork(
                items=[1, 2, 3],
                fn=stateful_fn,
                choose=lambda b: 0,
                tx=tx,
            )

        post = list(s.escape.fact.store.all_datoms())
        new = post[len(pre):]
        non_commit = [
            d for d in new if d.a != "persistence.txn/commit-id"
        ]
        assert non_commit == [], (
            f"bare s.txn.fork must not emit substrate facts; "
            f"got {[(d.e, d.a, d.v) for d in non_commit]}"
        )


# ---------------------------------------------------------------------------
# 7. Identity stability — the txn namespace returns the same instance
# ---------------------------------------------------------------------------


def test_s_txn_fork_namespace_identity_stable():
    """s.txn.fork accessed twice returns method bindings on the SAME
    namespace instance (per the curated-namespace identity contract).
    """
    with Substrate.open("memory") as s:
        ns1 = s.txn
        ns2 = s.txn
        assert ns1 is ns2
        # Method bindings are bound methods of the same namespace
        # instance, so their __self__ must match.
        assert s.txn.fork.__self__ is s.txn.fork.__self__
