"""Phase 2.2a — argparse parser for `python -m persistence.coder`.

Flags: `--task` (required), `--db-path` (optional, defaults to None →
in-memory substrate per CP1), `--provider`, `--model`, `--max-iters`.
`--confidence-threshold` is deferred to 2.3b/2.4a per design CP2 (the
class attribute exists on `Coder` and is constructor-pass-through
tested, but no CLI surface until behavior consumes it).
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.coder",
        description=(
            "persistence-coder skeleton (Phase 2.2a): _observe + _act "
            "+ run() loop + _should_escalate_* gates filled. "
            "Plan/branch escalation bodies stub to Phase 2.3a/2.3b."
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
    parser.add_argument(
        "--max-iters",
        type=int,
        default=20,
        help="Loop iteration cap (default 20). Tuning deferred to 2.4a CP2.",
    )
    return parser
