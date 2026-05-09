"""Phase 2.1a/b — CLI entry point for `python -m persistence.coder`.

Phase 2.1a: opens a Substrate (CP1: bare-string URI), constructs Coder, runs.
Phase 2.1b: detects/installs the chosen :llm/call provider handler before run.
Phase 2.4a (T1 LD-1): installs the :skill/* handler against a SkillLibrary
bound to the substrate's DB BEFORE the provider handler so a CLI-driven
coder run can perform :skill/define / :skill/lookup without `Unhandled`.

On `CoderStubNotImplemented` raises a clean stderr banner and exits 1.
Bare `NotImplementedError` from real 2.1b+ code propagates untouched.
"""

from __future__ import annotations

import argparse
import sys

from persistence.effect.handlers import make_skill_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.plan import SkillLibrary
from persistence.sdk import Substrate

from ._cli import build_parser
from ._provider import detect_or_explicit
from ._session import Coder, CoderStubNotImplemented


def _build_substrate_and_handlers(args: argparse.Namespace) -> Substrate:
    """Open the substrate and install effect handlers per the CLI args.

    Phase 2.4a T1 LD-1: extracts the substrate-build path from
    :func:`main` so it is testable without running the full coder loop.
    Mirrors the 2.3c.1 fixture pattern at
    ``tests/coder/test_skill_lookup_op.py::s_with_skill_handler``.

    Steps:

    1. Resolve the URI from ``--db-path`` (omitted → ``"memory"``);
       emit the in-memory stderr warning for parity with the original
       inline path.
    2. ``Substrate.open(uri)``.
    3. Install ``make_skill_handler`` against a fresh
       :class:`SkillLibrary` bound to ``substrate._db`` at
       ``position="bottom"`` (LD-1).
    4. Detect / install the LLM provider handler (or echo fallback)
       at ``position="bottom"`` per the existing 2.1b behavior.

    Caller is responsible for closing the returned substrate (the
    production path uses a ``with`` block; the test path uses
    ``substrate.close()`` in a ``finally``).
    """
    if args.db_path is None:
        print(
            "warning: no --db-path; using in-memory substrate "
            "(non-persistent — coder state will not survive process exit)",
            file=sys.stderr,
        )
        uri = "memory"
    else:
        uri = args.db_path

    handler, provider_name = detect_or_explicit(args.provider)
    if provider_name == "echo":
        print(
            "warning: no LLM provider available — using echo handler. "
            "Set ANTHROPIC_API_KEY or sign in to Claude Code. "
            "Run will exit on first stub.",
            file=sys.stderr,
        )
    else:
        print(f"using {provider_name} provider", file=sys.stderr)

    substrate = Substrate.open(uri)

    # T1 LD-1: skill handler BEFORE provider handler. Both install at
    # position="bottom" — the runtime resolves by op tag, not by stack
    # order, so coexistence is invariant.
    skill_lib = SkillLibrary(substrate._db)
    substrate.effect.install_handler(
        make_skill_handler(skill_lib, name="skill"),
        position="bottom",
    )

    if handler is None:
        substrate.effect.install_handler(
            make_echo_llm_handler(), position="bottom",
        )
    else:
        substrate.effect.install_handler(handler, position="bottom")

    return substrate


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Provider detection happens BEFORE substrate open inside the helper
    # so explicit-provider errors short-circuit cleanly without opening
    # a DB. (Helper internalizes both the provider stderr banner and the
    # URI/in-memory stderr banner for parity with the pre-T1 inline path.)
    try:
        substrate = _build_substrate_and_handlers(args)
        try:
            Coder(
                task=args.task,
                substrate=substrate,
                model=args.model,
                max_iters=args.max_iters,
            ).run()
        finally:
            substrate.close()
    except CoderStubNotImplemented as exc:
        print(f"persistence-coder skeleton: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
