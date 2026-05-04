"""Phase 2.1a/b — CLI entry point for `python -m persistence.coder`.

Phase 2.1a: opens a Substrate (CP1: bare-string URI), constructs Coder, runs.
Phase 2.1b: detects/installs the chosen :llm/call provider handler before run.

On `CoderStubNotImplemented` raises a clean stderr banner and exits 1.
Bare `NotImplementedError` from real 2.1b+ code propagates untouched.
"""

from __future__ import annotations

import sys

from persistence.sdk import Substrate

from ._cli import build_parser
from ._provider import detect_or_explicit
from ._session import Coder, CoderStubNotImplemented


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Provider detection happens BEFORE substrate open so explicit-provider
    # errors short-circuit cleanly without opening a DB.
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

    if args.db_path is None:
        print(
            "warning: no --db-path; using in-memory substrate "
            "(non-persistent — coder state will not survive process exit)",
            file=sys.stderr,
        )
        uri = "memory"
    else:
        uri = args.db_path

    try:
        with Substrate.open(uri) as substrate:
            if handler is None:
                from persistence.effect.handlers.raw import make_echo_llm_handler
                substrate.effect.install_handler(
                    make_echo_llm_handler(), position="bottom",
                )
            else:
                substrate.effect.install_handler(handler, position="bottom")
            Coder(
                task=args.task,
                substrate=substrate,
                model=args.model,
            ).run()
    except CoderStubNotImplemented as exc:
        print(f"persistence-coder skeleton: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
