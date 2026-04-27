"""Effect runtime — Effect primitive, Handler, Runtime, perform, mask, named.

Models paper §4.2: a handler stack H = [h_1 ... h_n] with h_1 outermost
dispatches operation κ to the outermost handler whose domain (wraps)
contains κ; that handler invokes continuation k, which delegates to the
remaining stack.

Koka-style additions:
- **Named handlers.** Every handler carries a ``name``; ``named(name, op, **args)``
  dispatches to a specific handler regardless of stack order.
- **Masked effects.** ``with mask(name):`` temporarily hides the named handler
  from the dispatch search — so a policy handler can itself call ``:llm/call``
  without re-triggering its own audit wrap.

No hidden globals across threads: the active runtime lives in a ``ContextVar``,
and each ``Runtime`` owns its handler list plus its own mask set.

v0.4 seam: ``persistence.plan.Dispatcher`` provides a handler-per-tag
registry over the plan AST walker. Effect handlers can be exposed to
plans by registering ``Dispatcher`` handlers that delegate to
``Runtime.perform``. The Runtime itself does NOT depend on
``plan.Dispatcher`` — the integration is opt-in at the call site.
See ``tests/effect/test_runtime_dispatcher_seam.py`` for the pattern.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Unhandled(RuntimeError):
    """No handler (or no *unmasked* handler) covers a performed op."""


# ---------------------------------------------------------------------------
# Effect primitive
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Effect:
    """A labeled operation plus its arguments.

    Intentionally minimal — the heavy lifting is in the handler stack.
    """

    op: str
    args: dict[str, Any]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


# Clause signature: (args, k, ctx) -> result
Clause = Callable[[dict[str, Any], Callable[[dict[str, Any]], Any], dict[str, Any]], Any]


@dataclass
class Handler:
    """A handler is a set of clauses keyed by op, plus a name and per-instance ctx.

    - ``name`` — identity for ``named(...)`` dispatch and ``mask(...)`` hiding.
    - ``wraps`` — the set of ops this handler *might* handle; ops outside this
      set are passed through.
    - ``clauses`` — map from op to Clause. ``clauses.keys() <= wraps`` by
      construction (checked in ``__post_init__``).
    - ``ctx`` — per-instance state. Mutated freely by the handler's own
      clauses but never shared across handler instances.
    """

    name: str
    wraps: set[str]
    clauses: dict[str, Clause]
    ctx: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Allow clauses.keys() to be a subset of wraps but not exceed it.
        extra = set(self.clauses) - set(self.wraps)
        if extra:
            raise ValueError(
                f"handler {self.name!r} declares clauses {extra!r} outside its wraps set"
            )

    def invoke(
        self,
        op: str,
        args: dict[str, Any],
        k: Callable[[dict[str, Any]], Any],
    ) -> Any:
        return self.clauses[op](args, k, self.ctx)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class Runtime:
    """A handler stack. ``handlers[0]`` is the innermost (raw); the last
    element is the outermost. This matches the prototype in spec §8, where
    ``reversed(_stack)`` walks outermost→innermost.

    Each Runtime instance carries its OWN ``ContextVar`` holding the current
    mask stack. ContextVars are ``asyncio.Task``-local and ``contextvars.copy_context()``-aware,
    so two concurrent tasks (or threads, if explicitly given a copied context)
    sharing the same Runtime see independent mask states. This was previously
    a plain ``list[set[str]]`` attribute — shared mutable state across every
    caller — and a single task's mask leaked into every other concurrent
    task's dispatch (ARIS Round 1 R2 F4).
    """

    def __init__(
        self,
        handlers: list[Handler] | None = None,
    ) -> None:
        self.handlers: list[Handler] = list(handlers or [])
        # Per-Runtime ContextVar. Default is the empty tuple (no masks).
        # Storing tuples (not lists) makes each push an immutable snapshot,
        # which is what ``ContextVar.set`` / ``reset`` expect.
        self._mask_var: contextvars.ContextVar[tuple[frozenset[str], ...]] = (
            contextvars.ContextVar(
                f"persistence_effect_runtime_masks_{id(self):x}",
                default=(),
            )
        )

    # ---------- introspection ----------

    def is_well_formed(self, catalog: Iterable[str]) -> bool:
        """Paper §4.2 Proposition 2.

        A stack is well-formed iff every op in ``catalog`` has at least one
        handler covering it.
        """
        return not self.uncovered_ops(catalog)

    def uncovered_ops(self, catalog: Iterable[str]) -> set[str]:
        covered: set[str] = set()
        for h in self.handlers:
            covered |= set(h.clauses)
        return set(catalog) - covered

    # ---------- dispatch ----------

    def _masked_names(self) -> set[str]:
        frames = self._mask_var.get()
        if not frames:
            return set()
        out: set[str] = set()
        for frame in frames:
            out |= frame
        return out

    # ---------- mask push/pop (used by the module-level `mask()` CM) --------

    def _push_mask(self, names: frozenset[str]) -> contextvars.Token:
        current = self._mask_var.get()
        return self._mask_var.set(current + (names,))

    def _pop_mask(self, token: contextvars.Token) -> None:
        self._mask_var.reset(token)

    def perform(
        self,
        op: str,
        args: dict[str, Any],
        *,
        txn_commit: str | None = None,
    ) -> Any:
        """Dispatch ``op`` through the stack, outermost first.

        v0.5.1 N2: ``txn_commit`` is the typed seam between
        ``persistence.txn._replay_effect_intents`` and the audit handler.
        When set, the commit_id is lifted onto ``args`` under the
        ``"_txn_commit"`` sentinel so the audit handler (and any future
        handler that wants to know which dosync drove the call) sees it.
        The audit handler pops the sentinel before hashing args, so
        ``args_hash`` stays a pure function of the call's arguments.
        Direct callers may still pass ``args["_txn_commit"]`` themselves;
        both paths converge on the same per-handler view.
        """
        if txn_commit is not None:
            args = {**args, "_txn_commit": txn_commit}
        masked = self._masked_names()
        # Build the ordered list of (handler, index) candidates — outermost first.
        # Handlers whose name is masked are skipped entirely.
        candidates: list[tuple[int, Handler]] = []
        for i in range(len(self.handlers) - 1, -1, -1):
            h = self.handlers[i]
            if h.name in masked:
                continue
            if op in h.clauses:
                candidates.append((i, h))
        if not candidates:
            raise Unhandled(f"no handler covers op {op!r}")

        # Continuation for position p = dispatch to the next candidate after p.
        def make_k(pos: int) -> Callable[[dict[str, Any]], Any]:
            def k(passed_args: dict[str, Any]) -> Any:
                if pos + 1 >= len(candidates):
                    raise Unhandled(
                        f"op {op!r} reached the bottom of the stack "
                        "(no raw handler for this op)"
                    )
                _, next_h = candidates[pos + 1]
                return next_h.invoke(op, passed_args, make_k(pos + 1))
            return k

        _, outer = candidates[0]
        return outer.invoke(op, args, make_k(0))

    def named_perform(self, name: str, op: str, args: dict[str, Any]) -> Any:
        """Dispatch ``op`` directly to the handler with ``name``.

        The handler is still permitted to call ``k``; when it does, the
        continuation walks the remaining handlers below it in the stack,
        which makes named dispatch a useful way to address e.g. an
        ``audit-archive`` sink that itself uses the clock/random handlers.
        """
        # Find the target handler index.
        target_index = None
        for i, h in enumerate(self.handlers):
            if h.name == name:
                target_index = i
                break
        if target_index is None:
            raise Unhandled(f"no handler named {name!r}")
        target = self.handlers[target_index]
        if op not in target.clauses:
            raise Unhandled(
                f"handler {name!r} does not handle op {op!r} "
                f"(handles {sorted(target.clauses)!r})"
            )

        # Continuation for named call = effectful dispatch through ops *below*
        # the target handler. We emulate this by building candidates starting
        # at ``target_index`` and walking downward.
        masked = self._masked_names()
        candidates: list[Handler] = []
        for i in range(target_index, -1, -1):
            h = self.handlers[i]
            if i != target_index and h.name in masked:
                continue
            if op in h.clauses:
                candidates.append(h)

        def make_k(pos: int) -> Callable[[dict[str, Any]], Any]:
            def k(passed_args: dict[str, Any]) -> Any:
                if pos + 1 >= len(candidates):
                    raise Unhandled(
                        f"named op {op!r} reached the bottom of the stack"
                    )
                return candidates[pos + 1].invoke(op, passed_args, make_k(pos + 1))
            return k

        return candidates[0].invoke(op, args, make_k(0))


# ---------------------------------------------------------------------------
# Context-local active runtime
# ---------------------------------------------------------------------------


_active: contextvars.ContextVar[Runtime | None] = contextvars.ContextVar(
    "persistence_effect_active_runtime", default=None
)


@contextlib.contextmanager
def with_runtime(rt: Runtime) -> Iterator[Runtime]:
    """Activate ``rt`` for the duration of the ``with`` block."""
    token = _active.set(rt)
    try:
        yield rt
    finally:
        _active.reset(token)


def _current() -> Runtime:
    rt = _active.get()
    if rt is None:
        raise Unhandled("no active runtime (wrap in `with with_runtime(...)`)")
    return rt


def perform(op: str, **args: Any) -> Any:
    """Perform an effect against the currently-active runtime.

    Inside a ``dosync`` body, raw ``perform()`` is forbidden —
    use ``tx.effect(op, **kwargs)`` instead so the call is queued as
    an intent and atomically replayed at commit time. Calling raw
    ``perform()`` inside a dosync raises ``EffectInIoBlock``.
    """
    # Late import to avoid circular dependency:
    # txn imports DB; DB does not import txn at module level.
    # This guard check is the single point of cross-module coupling
    # in the runtime path and is unconditionally cheap (one ContextVar
    # read).
    from persistence.txn.intents import is_in_dosync
    from persistence.txn.errors import EffectInIoBlock

    if is_in_dosync():
        raise EffectInIoBlock(
            f"raw effect.perform({op!r}, ...) called inside a dosync "
            f"body. Use tx.effect({op!r}, **kwargs) so the call is "
            f"queued as an intent and atomically replayed at commit."
        )
    return _current().perform(op, args)


def named(name: str, op: str, **args: Any) -> Any:
    """Perform an effect targeting a specific named handler."""
    return _current().named_perform(name, op, args)


@contextlib.contextmanager
def mask(*names: str) -> Iterator[None]:
    """Within the block, hide the named handlers from dispatch.

    Masking is cumulative: ``with mask("a"): with mask("b"):`` hides both.
    Masks are scoped to the current runtime *and* to the current
    ``ContextVar`` context — each ``asyncio.Task`` sees its own mask stack,
    so two concurrent tasks sharing one Runtime do not leak mask state into
    each other (ARIS Round 1 R2 F4).
    """
    rt = _current()
    frame = frozenset(names)
    token = rt._push_mask(frame)
    try:
        yield
    finally:
        rt._pop_mask(token)
