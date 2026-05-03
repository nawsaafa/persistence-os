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
            "substrate. v0.9.0a1 skeleton (Phase 2.1a): runs no-op loop, "
            "raises CoderStubNotImplemented on the first un-filled stub."
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
    return parser
