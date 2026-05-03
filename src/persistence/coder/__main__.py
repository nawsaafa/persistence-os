"""Phase 2.1a — CLI entry point for `python -m persistence.coder`.

Opens a Substrate (per CP1: bare-string URI), constructs a Coder,
runs it. On `CoderStubNotImplemented` raises a clean stderr banner
and exits 1. Bare `NotImplementedError` from real 2.1b+ code
propagates untouched (R1 fix-1 — narrow catch).
"""

from __future__ import annotations

import sys

from persistence.sdk import Substrate

from ._cli import build_parser
from ._session import Coder, CoderStubNotImplemented


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
            Coder(
                task=args.task,
                substrate=substrate,
            ).run()
    except CoderStubNotImplemented as exc:
        print(f"persistence-coder skeleton: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
