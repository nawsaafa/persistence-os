"""Phase 2.1a/b — CLI entry point for `python -m persistence.coder`.

Phase 2.1a: opens a Substrate (CP1: bare-string URI), constructs Coder, runs.
Phase 2.1b: detects/installs the chosen :llm/call provider handler before run.
Phase 2.4a (T1 LD-1): installs the :skill/* handler against a SkillLibrary
bound to the substrate's DB BEFORE the provider handler so a CLI-driven
coder run can perform :skill/define / :skill/lookup without `Unhandled`.
Phase 2.4a (T4 LD-4): when ``PERSISTENCE_AUDIT_KEY=file:///<abs>/key.pem``
is set, parses the URI, loads the PEM-encoded Ed25519 private key,
derives a stable signer_id, and threads ``(signer_id, raw_priv_bytes)``
through ``Substrate.open(audit_signer=...)`` so every emitted AuditEntry
is signed. Unset env preserves pre-2.4a unsigned behaviour.

On `CoderStubNotImplemented` raises a clean stderr banner and exits 1.
Bare `NotImplementedError` from real 2.1b+ code propagates untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from urllib.parse import urlparse

from persistence.effect.handlers import make_skill_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.plan import SkillLibrary
from persistence.sdk import Substrate

from ._cli import build_parser
from ._provider import detect_or_explicit
from ._session import Coder, CoderStubNotImplemented


_AUDIT_KEY_ENV = "PERSISTENCE_AUDIT_KEY"


def _load_audit_signer_from_env() -> tuple[str, bytes] | None:
    """Resolve the optional Ed25519 signer from ``PERSISTENCE_AUDIT_KEY``.

    Returns ``None`` when the env var is absent (unsigned-mode default).
    Raises ``SystemExit`` on unsupported URI schemes or unreadable PEM
    files — env-set-but-broken is a hard fail at CLI bootstrap rather
    than a silent fall-through to unsigned, since the operator's intent
    was clearly "sign this run" and a silent downgrade would defeat the
    accountability contract.

    URI scheme: ``file:///absolute/path/to/key.pem`` (RFC 8089
    three-slash absolute-path form). Other schemes raise SystemExit.

    signer_id derivation: ``"ed25519:<sha256(pem_bytes)[:16]>"`` —
    stable per-key label hashing the on-disk PEM bytes (NOT the
    extracted raw private key) so the same PEM file always maps to the
    same id across processes.

    Returns ``(signer_id, raw_32_byte_priv)`` per the
    :func:`persistence.effect.handlers.audit.make_audit_handler`
    contract — see ``handlers/audit.py:488-518`` and
    ``effect/_signing.py:60-74`` for the raw-bytes invariant.
    See CHANGELOG-coder.md for the 2.4a LD-4 signer_id convention.
    """
    raw = os.environ.get(_AUDIT_KEY_ENV)
    if raw is None or raw == "":
        return None

    parsed = urlparse(raw)
    if parsed.scheme != "file":
        raise SystemExit(
            f"error: {_AUDIT_KEY_ENV}={raw!r} — unsupported URI scheme "
            f"{parsed.scheme!r}; only 'file:///<abs>/key.pem' is supported"
        )
    # RFC 8089 three-slash form: ``file:///abs/path`` → netloc='' + path='/abs/path'.
    # A two-slash form (``file:/abs/path``) would put the path on .path
    # too but is non-canonical; we accept both since urlparse normalizes.
    pem_path = parsed.path
    if not pem_path:
        raise SystemExit(
            f"error: {_AUDIT_KEY_ENV}={raw!r} — empty path after scheme"
        )

    try:
        with open(pem_path, "rb") as f:
            pem_bytes = f.read()
    except OSError as exc:
        raise SystemExit(
            f"error: {_AUDIT_KEY_ENV}={raw!r} — cannot read PEM file "
            f"at {pem_path!r}: {exc}"
        ) from exc

    # Extract raw 32-byte Ed25519 private key from the PEM. The signing
    # primitive at persistence.effect._signing.sign requires raw bytes;
    # PEM/PKCS8 wrapping is unwrapped here at the CLI boundary so the
    # substrate-internal contract stays raw-only.
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        loaded = serialization.load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:  # noqa: BLE001 — surface any PEM-load error as SystemExit
        raise SystemExit(
            f"error: {_AUDIT_KEY_ENV}={raw!r} — failed to load PEM private "
            f"key: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(loaded, Ed25519PrivateKey):
        raise SystemExit(
            f"error: {_AUDIT_KEY_ENV}={raw!r} — PEM contains a "
            f"{type(loaded).__name__}, not an Ed25519PrivateKey"
        )

    raw_priv = loaded.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # 2.4a LD-4 signer_id convention; see CHANGELOG-coder.md.
    signer_id = f"ed25519:{hashlib.sha256(pem_bytes).hexdigest()[:16]}"
    return (signer_id, raw_priv)


def _build_substrate_and_handlers(
    args: argparse.Namespace,
) -> tuple[Substrate, str]:
    """Open the substrate and install effect handlers per the CLI args.

    Phase 2.4a T1 LD-1: extracts the substrate-build path from
    :func:`main` so it is testable without running the full coder loop.
    Mirrors the 2.3c.1 fixture pattern at
    ``tests/coder/test_skill_lookup_op.py::s_with_skill_handler``.

    Phase 2.4a T4 LD-4: resolves the optional Ed25519 signer from
    ``PERSISTENCE_AUDIT_KEY`` and threads it through
    ``Substrate.open(audit_signer=...)``. Unset env → unsigned-mode
    (None passthrough). See :func:`_load_audit_signer_from_env`.

    Steps:

    1. Resolve the URI from ``--db-path`` (omitted → ``"memory"``);
       emit the in-memory stderr warning for parity with the original
       inline path.
    2. Resolve the optional ``audit_signer`` from env (T4 LD-4).
    3. ``Substrate.open(uri, audit_signer=...)``.
    4. Install ``make_skill_handler`` against a fresh
       :class:`SkillLibrary` bound to ``substrate._db`` at
       ``position="bottom"`` (LD-1).
    5. Detect / install the LLM provider handler (or echo fallback)
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

    # T4 LD-4: env-keyed Ed25519 signer (None when env is unset).
    audit_signer = _load_audit_signer_from_env()
    if audit_signer is not None:
        signer_id, _ = audit_signer
        print(f"audit signer: {signer_id}", file=sys.stderr)

    substrate = Substrate.open(uri, audit_signer=audit_signer)

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

    return substrate, provider_name


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Phase 2.4b.1 LD-1 (R0-fold I1): pre-bind so the except ValueError
    # below can safely reference provider_name even if
    # _build_substrate_and_handlers itself raises ValueError before the
    # unpacking succeeds. In that case provider_name stays None → mask
    # check is False → re-raise (substrate-construction ValueErrors are
    # real programmer errors and must propagate).
    provider_name: str | None = None

    # Provider detection happens BEFORE substrate open inside the helper
    # so explicit-provider errors short-circuit cleanly without opening
    # a DB. (Helper internalizes both the provider stderr banner and the
    # URI/in-memory stderr banner for parity with the pre-T1 inline path.)
    try:
        substrate, provider_name = _build_substrate_and_handlers(args)
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
    except ValueError:
        # Phase 2.4b.1 LD-1 (codex consensus Option C, R0-fold I1):
        # narrow banner-mask. Only swallow ValueError when echo handler
        # is in use (no real LLM provider). Other ValueErrors are real
        # programmer errors and must propagate. Substrate-construction
        # ValueErrors → provider_name stays None → falls through to raise.
        if provider_name == "echo":
            print(
                "persistence-coder: echo handler can't drive a real "
                "agent loop. Set ANTHROPIC_API_KEY or sign in to Claude "
                "Code, then re-run.",
                file=sys.stderr,
            )
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
