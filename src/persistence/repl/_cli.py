"""CLI wrapper for ``persistence.repl`` token management.

Subcommands::

    python -m persistence.repl mint --caps inspect:read,edit:write \\
        [--expires <iso>] [--label "..."] [--db-path /path/to/store.db]

    python -m persistence.repl list [--db-path /path/to/store.db]

    python -m persistence.repl revoke <token_id> [--db-path /path/to/store.db]

The ``mint`` subcommand prints the raw token to stdout (the operator's
ONLY chance to capture it; the raw string is not persisted). ``list``
prints one row per active (non-revoked) token. ``revoke`` writes the
revoke datom; idempotent on repeat. See design doc §6.2 (issuance) /
§6.4 (revocation) and the §10 D1 task description.

Clock seam: ``--clock-iso`` overrides the default ``datetime.now(UTC)``
so deterministic CI / replay tests can pin the issuance time. Without
``--clock-iso``, the CLI samples the wall clock via the substrate's
``:clock/now`` handler-equivalent (a thin shim — production servers
mount the full handler stack via ``WSServer.serve``).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Callable

from persistence.fact import DB, InMemoryStore, SQLiteStore

from ._caps import (
    Capability,
    list_tokens,
    mint_token,
    revoke_token,
    store_token,
)


def _parse_caps(s: str) -> frozenset[Capability]:
    """Parse ``"inspect:read,edit:write"`` → frozenset of Capabilities.

    Whitespace tolerated; empty entries skipped. Raises
    ``UnknownCapability`` (via ``Capability.__post_init__``) on any
    out-of-set ``(op, qualifier)`` pair.
    """
    out: set[Capability] = set()
    for entry in s.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f"capability spec {entry!r} missing ':' (expected 'op:qualifier')"
            )
        op, qualifier = entry.split(":", 1)
        out.add(Capability(op.strip(), qualifier.strip()))
    return frozenset(out)


def _load_db_from_path(db_path: str | None) -> DB:
    """Open a ``DB`` against the requested path.

    ``--db-path`` resolves to a SQLite-backed store; ``None`` falls back
    to an in-memory store (useful for ``--help`` / smoke tests but
    obviously non-persistent — the operator gets a warning on stderr).
    """
    if db_path is None:
        print(
            "warning: no --db-path; using ephemeral InMemoryStore "
            "(tokens will not survive process exit)",
            file=sys.stderr,
        )
        return DB(InMemoryStore())
    return DB(SQLiteStore(path=db_path))


def _make_clock(clock_iso: str | None) -> Callable[[], datetime]:
    """Return a ``runtime_clock`` callable.

    With ``--clock-iso``, returns a deterministic constant-clock — the
    same value every invocation, so issuance + listing in the same CLI
    process pin to one timestamp. Without it, samples the wall clock
    via the substrate's authorised path. The CLI is one of the few
    user-facing entry points that bootstraps without a mounted handler
    runtime; ``noqa: wall-clock`` annotates the single sample.
    """
    if clock_iso is not None:
        pinned = datetime.fromisoformat(clock_iso)
        return lambda: pinned
    return lambda: datetime.now(timezone.utc)  # noqa: wall-clock


def _cmd_mint(args: argparse.Namespace) -> int:
    db = _load_db_from_path(args.db_path)
    clock = _make_clock(args.clock_iso)
    caps = _parse_caps(args.caps)
    expires_at = (
        datetime.fromisoformat(args.expires) if args.expires else None
    )
    token = mint_token(
        caps=caps,
        expires_at=expires_at,
        label=args.label or "",
    )
    store_token(db, token, runtime_clock=clock)
    # Print the raw token to stdout — operator's only chance to capture it.
    print(token.token_str)
    print(f"# token_id: {token.token_id}", file=sys.stderr)
    print(f"# label: {token.cap_set.label!r}", file=sys.stderr)
    if expires_at:
        print(f"# expires_at: {expires_at.isoformat()}", file=sys.stderr)
    else:
        print("# expires_at: never", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    db = _load_db_from_path(args.db_path)
    clock = _make_clock(args.clock_iso)
    rows = list_tokens(db, runtime_clock=clock)
    if not rows:
        print("(no active tokens)", file=sys.stderr)
        return 0
    for row in rows:
        # Fixed shape per §10 D1 / MINOR-4: one row per line, columns
        # token_id / label / expires / caps_summary tab-separated for
        # easy operator-grep / cut chaining.
        print(
            "\t".join([
                row["token_id"],
                row["label"] or "(no-label)",
                row["expires_at_iso"],
                row["caps_summary"] or "(no-caps)",
            ])
        )
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    db = _load_db_from_path(args.db_path)
    clock = _make_clock(args.clock_iso)
    revoke_token(db, args.token_id, runtime_clock=clock)
    # Idempotent on repeat — a second revoke writes another datom but
    # validate_token still sees ``repl/revoked == True``.
    print(f"revoked: {args.token_id}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.repl",
        description="Manage REPL capability tokens.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite-backed datom store. "
             "If omitted, uses an in-memory store (non-persistent).",
    )
    parser.add_argument(
        "--clock-iso",
        default=None,
        help="Pin runtime_clock to this ISO-8601 timestamp. "
             "If omitted, samples the wall clock.",
    )

    subs = parser.add_subparsers(dest="command", required=True)

    p_mint = subs.add_parser("mint", help="Issue a fresh token.")
    p_mint.add_argument(
        "--caps",
        required=True,
        help="Comma-separated 'op:qualifier' pairs "
             "(e.g. 'inspect:read,edit:write').",
    )
    p_mint.add_argument(
        "--expires",
        default=None,
        help="ISO-8601 expiry timestamp. Omit for no expiry.",
    )
    p_mint.add_argument(
        "--label",
        default=None,
        help="Human-readable label for the token (audit-visible).",
    )
    p_mint.set_defaults(func=_cmd_mint)

    p_list = subs.add_parser("list", help="List active tokens.")
    p_list.set_defaults(func=_cmd_list)

    p_revoke = subs.add_parser("revoke", help="Revoke a token.")
    p_revoke.add_argument(
        "token_id",
        help="The 16-hex token_id to revoke.",
    )
    p_revoke.set_defaults(func=_cmd_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
