"""Phase 2.1a — argparse parser for `python -m persistence.coder`.

Two flags: `--task` (required), `--db-path` (optional, defaults to
None → in-memory substrate per CP1). `--confidence-threshold` is
deferred to 2.3b/2.4a per design CP2 (the class attribute exists on
`Coder` and is constructor-pass-through tested, but no CLI surface
until behavior consumes it).
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.coder",
        description=(
            "persistence-coder MVP — agent built on the persistence-os "
            "substrate. v0.9.0a1 (Phase 2.1b): _decide body lights up, "
            "first :llm/messages + :llm/decision datoms emit. Run still "
            "raises CoderStubNotImplemented on _observe (Phase 2.2a)."
        ),
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Task description for the agent.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            'Substrate URI ("memory" / "sqlite:///<abs-path>"). '
            "Omitted → in-memory + stderr warning."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "anthropic", "claude-code"],
        default="auto",
        help=(
            "LLM provider. 'auto' (default) tries claude-code → anthropic → echo. "
            "'anthropic' uses the Anthropic API (requires ANTHROPIC_API_KEY). "
            "'claude-code' uses claude-agent-sdk (Max subscription via host session)."
        ),
    )
    parser.add_argument(
        "--model",
        default="claude-opus-4-7",
        help="Model identifier passed to the provider. Default: claude-opus-4-7.",
    )
    return parser
