"""Phase 2.4a — CLI wiring integration tests.

G1 — `__main__.main()`'s substrate bootstrap installs `make_skill_handler`
so a CLI-driven coder run can perform `:skill/define` / `:skill/lookup`.

Pattern: extract the substrate-build path from `main()` into a testable
helper `_build_substrate_and_handlers(args)` and invoke it directly with
synthesized argparse args. The helper does the same `Substrate.open(uri)`
+ skill-handler install + provider-handler install that `main()` runs
inline (T1 LD-1). The test then performs `:skill/lookup` for an
unregistered skill on the returned substrate and asserts
`SkillNotFound` — which is what the LD-1-wired skill handler raises.

Falsifiability: if LD-1 is NOT wired (skill handler is not installed by
`_build_substrate_and_handlers`), the perform call hits
`Unhandled("no handler covers op ':skill/lookup'")` from
`persistence.effect.runtime` (NOT `SkillNotFound`), and
`pytest.raises(SkillNotFound)` fails. This is the G1 falsifiability
contract per design § G1.

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
"""
from __future__ import annotations

import argparse

import pytest

from persistence.coder import _provider as _provider_mod
from persistence.coder import __main__ as coder_main
from persistence.coder.__main__ import _build_substrate_and_handlers
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
