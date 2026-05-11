"""CLI entrypoint: `python -m persistence.orchestrate emit ...`

Subcommands:
    emit  --chain <path>  --out <dir>   Emit a 4-file orchestrator skill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn


def _cmd_emit(args: argparse.Namespace) -> int:
    chain_path = args.chain.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()

    if not chain_path.exists():
        print(f"chain file not found: {chain_path}", file=sys.stderr)
        return 1

    chain_src = chain_path.read_text()
    chain = parse_chain_edn(chain_src)
    emit_orchestrator_skill(chain, out_dir)

    print(f"emitted 4-file orchestrator skill at {out_dir}:")
    for name in ("SKILL.md", "chain.edn", "preflight.toml", "orchestrate.py"):
        print(f"  {out_dir / name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.orchestrate",
        description="persistence-orchestrate — emit installable chain orchestrator skills.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    emit_p = sub.add_parser("emit", help="Emit a 4-file orchestrator skill")
    emit_p.add_argument("--chain", type=Path, required=True,
                         help="Path to a chain.edn source file.")
    emit_p.add_argument("--out", type=Path, required=True,
                         help="Output directory for the emitted skill.")
    emit_p.set_defaults(func=_cmd_emit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
