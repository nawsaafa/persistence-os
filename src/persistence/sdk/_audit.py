"""SDK-internal audit helpers (escape-hatch telemetry).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-1 W2 + W3 NIT-5, the first time per-session an adapter reaches into
an :class:`~persistence.sdk.Substrate` escape-hatch attribute, the SDK
emits exactly one ``:sdk/escape-hatch-access`` :class:`AuditEntry`. This
module owns the helper that builds that entry.

The helper is decorated :func:`~persistence.sdk._stability.experimental`
because the audit-entry shape itself is **not** part of the v0.8 contract
(W3 NIT-5 closure). Adapter authors who parse these entries do so at
their own risk; the spec generator (G7 / SDK5) excludes them from the
contract surface.

The audit-entry payload shape is::

    {
        "module":         <one of "fact" / "effect" / "plan" / "replay"
                           / "txn" / "spec" / "repl">,
        "caller_filename": <best-effort sys._getframe walk>,
        "session_id":     <Substrate session id>,
    }

This is the entire payload ADR-1 commits to; downstream code reading it
should treat unknown keys as additive and never depend on key ordering.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from persistence.sdk._stability import experimental


# The seven curated escape-hatch module names. Used by the helper to
# validate inputs at audit-entry-emission time so a typo in caller code
# is caught loudly rather than silently emitting a malformed shape.
ESCAPE_HATCH_MODULES = frozenset(
    {"fact", "effect", "plan", "replay", "txn", "spec", "repl"}
)


@experimental(
    reason=(
        "audit-entry shape for :sdk/escape-hatch-access is non-contract "
        "per ADR-1 W3 NIT-5; may evolve during Phase-2 dogfooding."
    ),
)
def build_escape_hatch_payload(
    module: str,
    *,
    session_id: str,
    caller_depth: int = 2,
) -> dict[str, Any]:
    """Build the ``:sdk/escape-hatch-access`` audit-entry payload dict.

    The helper does NOT emit the entry to any sink — that is the caller's
    responsibility (the :class:`Substrate` keeps a per-session entry list
    and dedupe set, then defers actual sink-routing to ADR-4's audit
    chain when SDK3 wires the MCP audit handler).

    Args:
        module: one of the seven escape-hatch module names; raises
            :class:`ValueError` otherwise.
        session_id: the :class:`Substrate` session id; copied verbatim
            into the payload so the entry survives session-id-keyed
            deduplication. Must be non-empty.
        caller_depth: stack-frame distance from the caller's call-site
            to the actual adapter source line; default ``2`` means
            "two frames up from this helper" which is the right depth
            when the helper is called from a property getter on
            :class:`Substrate.escape`. Tests override this to validate
            the resolution.

    Returns:
        the canonical payload dict per ADR-1 W3 NIT-5.

    Raises:
        ValueError: if ``module`` is not one of the seven allowed names
            or ``session_id`` is empty.
    """
    if module not in ESCAPE_HATCH_MODULES:
        raise ValueError(
            f"build_escape_hatch_payload: unknown module {module!r}; "
            f"must be one of {sorted(ESCAPE_HATCH_MODULES)!r}"
        )
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(
            "build_escape_hatch_payload: session_id must be a non-empty "
            f"string, got {session_id!r}"
        )
    return {
        "module": module,
        "caller_filename": _resolve_caller_filename(caller_depth),
        "session_id": session_id,
    }


def _resolve_caller_filename(depth: int) -> str:
    """Best-effort resolution of the caller's source filename via
    ``sys._getframe``. Returns ``"<unknown>"`` if the frame walk fails
    (e.g. on alternate Python implementations that don't expose frames).

    The helper short-circuits to a basename so the audit log doesn't
    leak the user's full home-directory path on every escape-hatch
    access; adapter authors who need the absolute path can add an
    `inspect`-driven post-processor at their own audit sink.
    """
    try:
        frame = sys._getframe(depth)
    except (AttributeError, ValueError):
        return "<unknown>"
    filename = getattr(frame, "f_code", None)
    if filename is None or not getattr(filename, "co_filename", ""):
        return "<unknown>"
    return os.path.basename(filename.co_filename)


__all__ = [
    "ESCAPE_HATCH_MODULES",
    "build_escape_hatch_payload",
]
