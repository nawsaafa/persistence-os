"""Phase 2.4b G5 — site-6 W3-rescope xfail-strict marker.

LD-4 site 6 (``src/persistence/coder/_searcher.py:591`` —
``started_at_ms = time.time_ns() // 1_000_000``) is DEFERRED from the
2.4b sweep because migrating it requires a representation contract:
``time.time_ns() // 1_000_000`` is an integer; the obvious
``int(dt.timestamp() * 1000)`` path introduces floats and a near-
boundary 1ms drift risk that would break replay byte-identity over
history.

This test is the falsifiability anchor for the rescope. It installs
``make_fixed_clock_handler(ts=42.0)`` on the substrate, runs MCTS via
``_escalate_branch_body``, and asserts the recorded ``mcts/started-at``
datom equals ``42_000`` — what ``started_at_ms`` WOULD be if site 6
routed through ``:sys/now``-and-converted-to-int-ms.

**Today: XFAIL.** ``time.time_ns()`` bypasses the clock handler
entirely, so installing a fixed clock has no effect on
``started_at_ms`` — the recorded value is ``time.time_ns() // 1e6``
at the moment of the test run (e.g. ~``1747000000000`` in 2026).
``recorded == 42_000`` fails sharply (~1.7 trillion ms delta).

**Future-phase flip.** When a follow-up phase lands integer-ms
conversion that routes through the clock handler (either
``:sys/now -> int(.timestamp() * 1000)`` with explicit floor/round
policy, or a new ``:sys/now-ms`` substrate op returning ``int``
directly), this test flips PASS. If that phase forgets to remove
the xfail decorator, XPASS-strict triggers.

See ``docs/plans/2026-05-11-phase-2.4b-sys-now-design.md`` §LD-4
site 6 + codex consensus Q2 verdict
(REJECT-FOR-OPTION-1-4-NOW-DEFER-5).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.sdk import Substrate


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Site 6 W3 rescope — _searcher.py:591 uses time.time_ns() // "
        "1_000_000 for started_at_ms, bypassing the clock handler. "
        "A follow-up phase must (a) define an integer-only ms "
        "conversion contract, (b) route site 6 through it (either "
        ":sys/now -> int(.timestamp() * 1000) with explicit floor "
        "semantics, or a new :sys/now-ms substrate op returning int "
        "directly), (c) REMOVE this xfail decorator. XPASS-strict "
        "catches incomplete ship if the fix lands but the decorator "
        "stays. See Phase 2.4b design §LD-4 site 6 + codex consensus "
        "Q2 verdict (REJECT-FOR-OPTION-1-4-NOW-DEFER-5)."
    ),
)
def test_mcts_started_at_ms_deterministic_under_fixed_clock(
    tmp_path: Path,
) -> None:
    """G5 — under ``make_fixed_clock_handler(ts=42.0)``, the recorded
    ``mcts/started-at`` datom must equal ``42_000`` (deterministic
    int-ms via the clock handler).

    Falsifiability today:
      - ``_searcher.py:591`` synthesises ``started_at_ms`` via
        ``time.time_ns() // 1_000_000`` — bypasses ``:clock/now``.
      - ``make_fixed_clock_handler(ts=42.0)`` substitutes ``:clock/now``
        only; nothing replaces ``time.time_ns()``.
      - So ``started_at_ms`` lands as real-wall-clock-ms (e.g.
        ~``1747000000000`` in 2026), NOT ``42_000``.
      - Datom ``a="mcts/started-at"`` carries this real-ms value;
        the equality assertion fails sharply (~1.7 trillion ms delta).
      - xfail-strict absorbs.

    Falsifiability under the future fix:
      - Site 6 routes through ``:sys/now`` (or ``:sys/now-ms``) with
        integer-only conversion -> ``started_at_ms`` reads ``42_000``
        because the fixed clock returns ``42.0`` epoch seconds.
      - Datom ``v == 42_000``, assertion passes, XPASS-strict triggers
        if the xfail decorator stays.
    """
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    with Substrate.open("memory") as s:
        # Replace the canonical system clock with a fixed clock at
        # epoch 42.0. install_handler is idempotent on name="clock"
        # per sdk/_facade.py:143-166, so this REPLACES the default
        # system-clock handler installed by canonical_audit_stack.
        # Under the FUTURE fix, started_at_ms would read 42_000 from
        # the clock-handler-routed ms op; today the wall-clock
        # time.time_ns() bypasses this handler and produces real-now-ms.
        s.effect.install_handler(make_fixed_clock_handler(ts=42.0))
        s.effect.install_handler(
            make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
            position="bottom",
        )

        # Minimal seed plan + minimal scripted LLM that yields a
        # 1-iter no-op MCTS. Enough to trigger the
        # _searcher.py:591 wall-clock read + datom emission.
        seed_path = str(scratch_dir / "seed.txt")
        seed_edn = (
            f'[:seq {{}} [:fs/write {{:path "{seed_path}" '
            ':bytes_or_text "s"}]]'
        )

        def call_fn(*, model, messages, tools=None, temperature=None,
                    max_tokens=None):
            tool_name = tools[0]["name"] if tools else ""
            if tool_name == "emit_branch_proposals":
                # No proposals -> MCTS terminates quickly via the
                # all_evaluations_failed / no-children path. We only
                # care about the started_at_ms recording, not the
                # search outcome.
                return {
                    "tool_calls": [{"input": {"proposals": []}}],
                    "text": "",
                    "usage": {"total_tokens": 1},
                }
            if tool_name == "emit_branch_score":
                return {
                    "tool_calls": [{"input": {"score": 0.5}}],
                    "text": "",
                    "usage": {"total_tokens": 1},
                }
            return {"tool_calls": [], "text": "", "usage": {"total_tokens": 1}}

        s.effect.install_handler(
            make_callable_llm_handler(call_fn=call_fn),
            position="bottom",
        )

        class _CoderStub:
            def __init__(self):
                self.substrate = s
                self.model = "claude-test-2.4b-g5"
                self.skill_library = None

        coder = _CoderStub()
        # Run the search; ignore the outcome — we only care that the
        # started-at datom landed.
        try:
            _escalate_branch_body(
                coder,
                LLMDecision(
                    kind="branch",
                    confidence=0.9,
                    payload={
                        "seed_plan_edn": seed_edn,
                        "mcts_config": {"max_iter": 1, "expander_k": 1},
                    },
                ),
            )
        except Exception:
            # Any search-layer failure is fine — the started_at datom
            # is written BEFORE the search loop terminates either way
            # (mcts_search emits the search-summary group on entry).
            pass

        # Scan the fact-log for the mcts/started-at datom.
        # Per Datom.__post_init__ canonicalization (datom.py:159-177),
        # the attribute is the bare form "mcts/started-at" (no leading
        # colon). The value is the integer started_at_ms.
        started_at_datoms = [
            d for d in s._db.log()
            if d.a == "mcts/started-at" and d.op == "assert"
        ]
        assert started_at_datoms, (
            "no `mcts/started-at` datom emitted by _escalate_branch_body — "
            "MCTS search-summary group must include the started-at fact"
        )
        recorded_started_at_ms = started_at_datoms[0].v

        # The acceptance equality. Today this fails (real-now-ms vs
        # 42_000); under the future fix it passes.
        assert recorded_started_at_ms == 42_000, (
            f"started_at_ms = {recorded_started_at_ms!r}, expected 42_000 "
            f"(fixed-clock @ epoch=42.0 -> int-ms = 42_000). If "
            f"recorded_started_at_ms is a 2026+-shaped value "
            f"(~1_700_000_000_000), site 6 is still using "
            f"`time.time_ns() // 1_000_000` at _searcher.py:591; route "
            f"through :sys/now (or :sys/now-ms) with integer-only "
            f"conversion per the W3 rescope contract."
        )
