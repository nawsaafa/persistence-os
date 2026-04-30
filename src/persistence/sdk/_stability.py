"""Stability annotations for the v0.8 adapter contract.

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-5 + ADR-15 + ADR-16, every symbol re-exported from ``persistence.sdk``
carries machine-readable stability metadata via one of three decorators:

- :func:`stable`        — covered by the v0.8 contract; signature + return
                          shape are frozen across ``0.8.x`` patch releases
                          (see ADR-16 for the precise breaking-change rules).
- :func:`experimental`  — emits no warning at call time but is NOT covered
                          by the contract; may break at any release.
- :func:`deprecated`    — schedules removal at a named version; downstream
                          callers see a ``DeprecationWarning`` (and, when a
                          handler stack is wired in SDK2+, a
                          ``:sdk/deprecated-call`` audit entry).

Each decorator attaches a ``__sdk_stability__`` attribute on the wrapped
object (function, class, or method). The attribute is a plain ``dict`` so
it survives ``functools.wraps`` and remains JSON-serialisable for the
spec-doc generator (G7 / SDK5).

This module ships in SDK1; the spec-doc generator that consumes the metadata
ships in SDK5. SDK1's only contract is "the metadata is attached and reads
back the way the generator will expect."
"""
from __future__ import annotations

import functools
import warnings
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attach_metadata(target: Any, metadata: dict[str, Any]) -> Any:
    """Attach ``metadata`` to ``target.__sdk_stability__`` and return it.

    Idempotent on re-decoration only when the metadata matches; conflicting
    re-decoration raises ``TypeError`` so accidental double-stamping (e.g.
    ``@stable @experimental``) is loud at import time, not silent at
    call time.
    """
    existing = getattr(target, "__sdk_stability__", None)
    if existing is not None and existing != metadata:
        raise TypeError(
            f"sdk-stability conflict on {target!r}: existing={existing!r} "
            f"vs new={metadata!r}; a symbol may carry exactly one stability "
            f"decorator."
        )
    target.__sdk_stability__ = metadata
    return target


# ---------------------------------------------------------------------------
# @stable("v0.8")
# ---------------------------------------------------------------------------
def stable(
    version: str, note: Optional[str] = None
) -> Callable[[T], T]:
    """Mark ``target`` as part of the named substrate-version contract.

    Per ADR-7, the contract version is the substrate version (``"v0.8"``).
    Adapter authors who pin to ``@stable("v0.8")`` symbols receive the
    breaking-change promises in ADR-16.

    Args:
        version: contract version, e.g. ``"v0.8"``. Must be non-empty.
        note: optional free-form note (e.g. ``"substring+tag mode"`` for
            ``recall`` per ADR-8); recorded in metadata, surfaced by the
            spec-doc generator.

    Returns:
        decorator that attaches::

            __sdk_stability__ = {
                "level":   "stable",
                "version": <version>,
                "note":    <note or None>,
            }

    Raises:
        ValueError: if ``version`` is empty.
    """
    if not isinstance(version, str) or not version:
        raise ValueError(
            f"@stable: version must be a non-empty string, got {version!r}"
        )
    metadata = {"level": "stable", "version": version, "note": note}

    def decorator(target: T) -> T:
        return _attach_metadata(target, metadata)

    return decorator


# ---------------------------------------------------------------------------
# @experimental(reason=...)
# ---------------------------------------------------------------------------
def experimental(
    reason: Optional[str] = None,
) -> Callable[[T], T]:
    """Mark ``target`` as experimental — present in the API but NOT covered
    by the v0.8 stability contract.

    Per ADR-1 (W1 revision), the ``Substrate.fact`` / ``s.effect`` / etc.
    escape-hatch attribute getters are the canonical experimental surface:
    they exist for adopter ergonomics but the spec-doc generator excludes
    them from the contract output.

    Calling an experimental symbol emits no warning (by design — they are
    expected to be in the wild during dogfooding); the metadata exists so
    the spec generator can advertise them as such and so audit-emission
    helpers in later SDK tasks can route around them when desired.

    Args:
        reason: optional free-form reason (e.g.
            ``"escape hatch — see ADR-1"``); recorded in metadata.

    Returns:
        decorator that attaches::

            __sdk_stability__ = {
                "level":  "experimental",
                "reason": <reason or None>,
            }
    """
    metadata = {"level": "experimental", "reason": reason}

    def decorator(target: T) -> T:
        return _attach_metadata(target, metadata)

    return decorator


# ---------------------------------------------------------------------------
# @deprecated(replacement=..., since=...)
# ---------------------------------------------------------------------------
def deprecated(
    replacement: str,
    *,
    since: str,
    removal: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark ``target`` as deprecated, scheduled for removal.

    Per ADR-5, calling a ``@deprecated`` callable emits a Python
    ``DeprecationWarning`` at the call site. (The matching
    ``:sdk/deprecated-call`` audit entry lands in SDK2+ once the handler
    stack is wired; SDK1 ships the warning + metadata only — that's
    intentional, audit emission needs a live ``Substrate`` runtime, which
    SDK2 builds.)

    Args:
        replacement: short string pointing adopters at the replacement
            symbol or pattern, e.g. ``"use Substrate.fact.transact"``.
        since: substrate version that introduced the deprecation, e.g.
            ``"v0.8"``. Required keyword-only.
        removal: optional substrate version at which the symbol will be
            removed entirely, e.g. ``"v0.9"``.

    Returns:
        decorator that wraps ``target`` with a ``DeprecationWarning`` shim
        AND attaches::

            __sdk_stability__ = {
                "level":       "deprecated",
                "replacement": <replacement>,
                "since":       <since>,
                "removal":     <removal or None>,
            }

    Raises:
        ValueError: if ``replacement`` or ``since`` is empty.
    """
    if not isinstance(replacement, str) or not replacement:
        raise ValueError(
            f"@deprecated: replacement must be a non-empty string, "
            f"got {replacement!r}"
        )
    if not isinstance(since, str) or not since:
        raise ValueError(
            f"@deprecated: since must be a non-empty string, got {since!r}"
        )
    metadata = {
        "level": "deprecated",
        "replacement": replacement,
        "since": since,
        "removal": removal,
    }

    def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
        # Wrap so calls emit DeprecationWarning. functools.wraps preserves
        # __name__ / __doc__ / __wrapped__ / __module__; we then re-attach
        # __sdk_stability__ on the wrapper (the source of truth the spec
        # generator inspects).
        @functools.wraps(target)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            msg = (
                f"{target.__name__} is deprecated since {since}; "
                f"{replacement}"
            )
            if removal is not None:
                msg += f" (scheduled removal in {removal})"
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
            return target(*args, **kwargs)

        return _attach_metadata(wrapper, metadata)

    return decorator


__all__ = [
    "deprecated",
    "experimental",
    "stable",
]
