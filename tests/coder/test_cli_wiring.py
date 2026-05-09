"""Phase 2.4a — CLI wiring integration tests.

G1 — `__main__.main()`'s substrate bootstrap installs `make_skill_handler`
so a CLI-driven coder run can perform `:skill/define` / `:skill/lookup`.

G2 — A coder run launched through the CLI bootstrap path enforces the
default recursion budget (`MAX_LLM_CALL_DEPTH=3`) end-to-end. Triggering
4-deep nested ``:llm/call`` raises
``LLMRecursionBudgetExceeded(field="depth")`` propagated up through
``Coder.run()``. This exercises the FULL chain — canonical_audit_stack
install (Substrate.open default ``audit=True``), the dispatcher
middleware in ``_audit_stack._make_dispatcher_handler``, and
``Coder.run``'s per-iteration ``dispatcher_context(DispatcherContext())``
binding at ``_session.py:87``.

No production code change for G2 — FD-LD2 confirmed in T0 ARIS that
canonical_audit_stack + Coder.run already wire the enforcement path;
this test is an empirical pin against regression in either component.

Pattern: extract the substrate-build path from `main()` into a testable
helper `_build_substrate_and_handlers(args)` and invoke it directly with
synthesized argparse args. The helper does the same `Substrate.open(uri)`
+ skill-handler install + provider-handler install that `main()` runs
inline (T1 LD-1). The test then performs `:skill/lookup` for an
unregistered skill on the returned substrate and asserts
`SkillNotFound` — which is what the LD-1-wired skill handler raises.

Falsifiability:
  G1 — if LD-1 is NOT wired (skill handler is not installed by
       `_build_substrate_and_handlers`), the perform call hits
       `Unhandled("no handler covers op ':skill/lookup'")` from
       `persistence.effect.runtime` (NOT `SkillNotFound`), and
       `pytest.raises(SkillNotFound)` fails. This is the G1 falsifiability
       contract per design § G1.
  G2 (Class A) — if `Coder.run` drops the
       `with dispatcher_context(DispatcherContext()):` wrap at
       `_session.py:87`, then `current_dispatcher_context()` returns
       `None` at perform time → dispatcher middleware short-circuits to
       pass-through (`_audit_stack.py:303`) → 4-deep recursion succeeds
       → no exception → `pytest.raises(LLMRecursionBudgetExceeded)` fails.
       Verified manually by commenting out the `with` line in _session.py
       and observing the test transition from PASS to FAILED.
  G2 (Class B) — if `__main__.py`'s `_build_substrate_and_handlers` calls
       `Substrate.open(uri, audit=False)`, the canonical_audit_stack is
       never installed → no dispatcher middleware → 4-deep succeeds →
       test fails. (Verified empirically via the same monkeypatch-based
       flip used to confirm Class A.)

Forced spec deviations:
  FD-T1.1: design § G1 prescribes invoking ``main(argv=[..., "--provider",
    "echo"])`` but the live ``_cli.build_parser()`` rejects ``echo`` (its
    ``--provider`` choices are ``{auto, anthropic, claude-code}``); echo
    is the FALLBACK detect_or_explicit emits when ``auto`` finds no
    provider, not a CLI-surface choice. Resolution: synthesize the
    argparse ``Namespace`` directly and monkeypatch
    ``detect_or_explicit`` to return ``(None, "echo")`` deterministically
    so the test does not depend on whether ``claude-agent-sdk`` or
    ``ANTHROPIC_API_KEY`` is present in the test environment. The G1
    falsifiability is unchanged — the assertion still pivots on whether
    ``:skill/lookup`` reaches the skill handler vs raising ``Unhandled``.

  FD-T2.1: G2 overrides the bootstrap-installed ``raw-echo`` provider with
    a callable handler that recursively performs ``:llm/call`` from
    inside its ``call_fn``. To replace cleanly we install with
    ``name="raw-echo"`` (matching the helper's echo-floor handler name);
    ``install_handler`` is idempotent on ``name`` per
    ``sdk/_facade.py:149-150`` so the new handler displaces the existing
    one. Without name-matching, both handlers would coexist as
    ``:llm/call`` candidates and the inner echo would intercept first
    (outer→innermost dispatch order at ``runtime.py:194-219``); the
    nested-perform call_fn would never reach depth=4.
"""
from __future__ import annotations

import argparse

import pytest

from persistence.coder import _provider as _provider_mod
from persistence.coder import __main__ as coder_main
from persistence.coder._recursion import (
    LLMRecursionBudgetExceeded,
    MAX_LLM_CALL_DEPTH,
)
from persistence.coder._session import Coder
from persistence.coder.__main__ import _build_substrate_and_handlers
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.skill import SkillNotFound


def _make_args() -> argparse.Namespace:
    """Synthesize the argparse Namespace the helper consumes.

    Bypasses ``build_parser().parse_args(...)`` per FD-T1.1 (the parser
    does not accept ``--provider echo``).
    """
    return argparse.Namespace(
        task="noop",
        db_path=None,
        provider="auto",
        model="claude-opus-4-7",
        max_iters=1,
    )


def test_main_installs_skill_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """G1: __main__.main()'s substrate setup installs make_skill_handler.

    Falsifiability: if LD-1 isn't wired, `:skill/lookup` raises
    `Unhandled` (no handler covers op) instead of `SkillNotFound`.
    The test asserts the latter — we get `SkillNotFound` for an
    unknown skill, which means the handler IS installed and processed
    the op.
    """
    # FD-T1.1: pin detect_or_explicit to the echo-floor return so the
    # test outcome does not depend on the test machine's claude-code /
    # ANTHROPIC_API_KEY availability.
    monkeypatch.setattr(
        coder_main,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )
    monkeypatch.setattr(
        _provider_mod,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )

    args = _make_args()
    substrate = _build_substrate_and_handlers(args)
    try:
        with pytest.raises(SkillNotFound):
            substrate.effect.perform(
                ":skill/lookup",
                {"skill-id": "nonexistent"},
            )
    finally:
        substrate.close()


# ---------------------------------------------------------------------------
# G2 — Recursion budget enforced through the full CLI bootstrap → Coder.run
# ---------------------------------------------------------------------------


def test_cli_run_enforces_recursion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2: a coder run bootstrapped via `_build_substrate_and_handlers`
    enforces the default ``MAX_LLM_CALL_DEPTH=3`` budget end-to-end.

    Setup:
      1. Build the substrate via the same helper `main()` uses (T1 LD-1).
         This implicitly opens the substrate with ``audit=True`` (default)
         so the canonical_audit_stack including the dispatcher middleware
         is installed.
      2. Replace the bootstrap's ``raw-echo`` provider with a callable
         handler whose ``call_fn`` recursively performs ``:llm/call``
         until the dispatcher middleware's depth-bound check raises
         (per FD-T2.1, install with ``name="raw-echo"`` to displace the
         existing handler in place).
      3. Run ``Coder(task=..., substrate=..., max_iters=1).run()``;
         the outer ``_decide`` dispatch sets ``depth=1``; the call_fn's
         3 nested performs push ``depth`` to 2, 3, then 4 — which exceeds
         ``MAX_LLM_CALL_DEPTH=3`` and raises
         ``LLMRecursionBudgetExceeded(field="depth")``.

    The exception propagates: dispatcher middleware → outer perform →
    ``_decide`` → ``Coder.run()`` (no try/except around the loop body
    per ``_session.py:77-98``) → out to ``pytest.raises``.

    Falsifiability — Class A (``_session.py:87`` dispatcher_context wrap
    dropped): ``current_dispatcher_context()`` returns ``None`` at
    perform time → middleware short-circuits to pass-through → recursion
    succeeds without raise → test fails with "DID NOT RAISE". Verified
    manually by commenting out the with-block before commit.

    Falsifiability — Class B (``audit=False`` regression in
    ``_build_substrate_and_handlers``): canonical_audit_stack is not
    installed → no dispatcher middleware → recursion succeeds → test
    fails. Same observable.
    """
    # Stable echo-floor for the bootstrap step (avoids depending on the
    # test machine's claude-agent-sdk / ANTHROPIC_API_KEY availability).
    monkeypatch.setattr(
        coder_main,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )
    monkeypatch.setattr(
        _provider_mod,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )

    args = _make_args()
    substrate = _build_substrate_and_handlers(args)
    try:
        # Sanity-check the bound MAX value the test was designed against.
        # If the default changes, the recursion target below must follow.
        assert MAX_LLM_CALL_DEPTH == 3, (
            "G2 was calibrated to MAX_LLM_CALL_DEPTH=3; update the "
            "recursion target if the default changes."
        )

        # Recursive call_fn: each invocation re-enters :llm/call until
        # the dispatcher middleware raises. The dispatcher's enter_call
        # bumps depth BEFORE k(args) (per _audit_stack.py:329 followed
        # by k(args) at line 350), so by the time call_fn runs, depth is
        # already at 1 (outer) / 2 / 3. The 4th nested perform enters
        # the dispatcher, bumps depth to 4, exceeds MAX=3, and raises.
        nesting_target_depth = MAX_LLM_CALL_DEPTH + 1  # 4

        def recursive_call_fn(
            *,
            model: str,
            messages: list[dict],
            tools: list | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
        ) -> dict:
            # Defer the import so the closure captures the live binding;
            # mirrors the test_recursion_session_binding pattern.
            from persistence.coder._recursion import (
                current_dispatcher_context,
            )

            ctx = current_dispatcher_context()
            # Class A falsifiability: if the dispatcher_context wrap is
            # missing, ctx is None here and the recursion never gets
            # bound-checked. Don't assert — just don't recurse forever.
            # The outer pytest.raises(LLMRecursionBudgetExceeded) will
            # not match, and pytest reports DID NOT RAISE.
            if ctx is None or ctx.depth >= nesting_target_depth - 1:
                # ctx None: middleware is pass-through; bail with a
                # benign tool_call so Coder._parse_decision picks a
                # done=True act decision (run() returns cleanly).
                # ctx.depth has already entered the target's parent
                # frame; one more recursive perform pushes over MAX.
                if ctx is None:
                    # No dispatcher bound — degenerate path; emit a
                    # done act so the loop terminates without further
                    # recursion (Class A regression surfaces here).
                    return {
                        "tool_calls": [
                            {
                                "input": {
                                    "kind": "act",
                                    "confidence": 0.9,
                                    "payload": {
                                        "done": True,
                                        "op": ":fs/read",
                                    },
                                }
                            }
                        ]
                    }
                # ctx.depth == nesting_target_depth - 1: recurse one
                # more level to trigger the bound.
                substrate.effect.perform(
                    ":llm/call",
                    {
                        "model": model,
                        "messages": [{"role": "user", "content": "deep"}],
                    },
                )
                # Unreachable — the recursive perform raised.
                return {  # pragma: no cover
                    "text": "unreachable",
                    "usage": {"total_tokens": 1},
                }

            # Otherwise keep recursing.
            substrate.effect.perform(
                ":llm/call",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "nest"}],
                },
            )
            # Unreachable on the failing path — kept for type-check
            # quietness when ctx is exactly at the target boundary
            # (the nested perform either raises or returns a usage
            # dict that cascades back without being consumed by Coder).
            return {  # pragma: no cover
                "text": "unreachable",
                "usage": {"total_tokens": 1},
            }

        # Replace the bootstrap-installed ``raw-echo`` handler in place
        # (FD-T2.1: install with name="raw-echo" to displace).
        substrate.effect.install_handler(
            make_callable_llm_handler(
                call_fn=recursive_call_fn,
                name="raw-echo",
            ),
            position="bottom",
        )

        coder = Coder(
            task="trigger-recursion",
            substrate=substrate,
            model="claude-opus-4-7",
            max_iters=1,
        )

        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            coder.run()
        assert exc_info.value.field == "depth", (
            f"expected field='depth', got {exc_info.value.field!r}"
        )
        assert exc_info.value.limit == MAX_LLM_CALL_DEPTH
        assert exc_info.value.observed == nesting_target_depth
    finally:
        substrate.close()
