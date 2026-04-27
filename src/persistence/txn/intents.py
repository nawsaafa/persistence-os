"""Effect-intent envelope + dosync ContextVar guard.

Inside a dosync body, calls to ``tx.effect(op, **kwargs)`` queue an
``EffectIntent`` rather than dispatching immediately. The guard
ContextVar is read by the effect runtime to detect raw ``perform()``
calls inside a dosync (which would bypass intent-queuing and re-fire on
retry). Such calls raise ``EffectInIoBlock``.

Mirrors the existing ContextVar pattern in effect/runtime.py:254
(_active runtime). Async-task-local for free, no threading.local.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EffectIntent:
    """A queued effect call captured during a dosync body.

    op:     the canonical op string (e.g. ":llm/call", ":log/write")
    kwargs: the keyword args for the eventual runtime.perform call

    Intents are replayed at commit time in queue order. On retry, the
    intent log is discarded (effects never fired).
    """

    op: str
    kwargs: dict[str, Any]


# Per-context guard. When set to True, the active code path is inside
# a dosync body — raw effect.perform() calls must raise EffectInIoBlock.
_DOSYNC_GUARD: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "persistence_txn_dosync_guard",
    default=False,
)


def is_in_dosync() -> bool:
    """True if the current context is inside an active dosync body."""
    return _DOSYNC_GUARD.get()


def set_dosync_guard() -> contextvars.Token:
    """Activate the guard and return a token. Pair with clear_dosync_guard."""
    return _DOSYNC_GUARD.set(True)


def clear_dosync_guard(token: contextvars.Token) -> None:
    """Deactivate the guard using the token returned by set_dosync_guard."""
    _DOSYNC_GUARD.reset(token)


__all__ = [
    "EffectIntent",
    "is_in_dosync",
    "set_dosync_guard",
    "clear_dosync_guard",
]
