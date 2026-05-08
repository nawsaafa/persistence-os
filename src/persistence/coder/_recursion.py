"""Phase 2.3c.2 — :llm/call recursion dispatcher context (T1 scope).

Provides the per-dispatch :class:`DispatcherContext` that the audit
middleware (T3) and coder session loop (T4) thread through nested
``:llm/call`` invocations. T1 ships only the type plumbing + ContextVar
binding + bounds-check helpers; the 4-layer token enforcement (Layers
1-4 per LD1) integrates with the actual ``:llm/call`` middleware in T3.

Locked design decisions covered here (per
``docs/plans/2026-05-08-phase-2.3c.2-recursion-composition-design.md``):

  * **LD1 — DispatcherContext + RecursionBudget + LLMRecursionBudgetExceeded.**
    ``ctx.depth`` counts CURRENTLY-ACTIVE ``:llm/call``\\s on the stack
    INCLUDING the call about to start. Initial state: ``depth=0`` (no
    active calls). ``enter_call`` increments FIRST then checks
    ``ctx.depth > MAX_LLM_CALL_DEPTH`` (``>`` strict; equality allowed —
    with ``MAX=3`` the allowed depths are ``{1,2,3}``). ``exit_call``
    decrements depth; ``request_count`` is cumulative across the recursion
    tree and is NOT decremented.

  * **LD2 — SkillCycleDetected.** Subclass of
    :class:`persistence.plan._mcts._PlanCycleDetected` for narrower error
    attribution at the runtime layer (Layer B execution-time dynamic
    active-path check). Layer A search-time STATIC subtree check at
    ``_mcts.py:213-217`` keeps using the parent class directly.

  * **LD1 lifetime.** :class:`DispatcherContext` is bound at coder
    iteration entry (one ``coder.run()`` cycle) and persists across
    non-``:llm/call`` ops (``:fs/read``, ``:shell/exec``, ``:git/diff``,
    etc.) interleaved between calls. Reset between coder iterations.

T1 explicitly does NOT:

  * modify ``audit.py`` (T2 — AuditEntry parent_audit_entry_id field add)
  * modify ``_audit_stack.py`` middleware (T3)
  * modify ``_session.py`` (T4 — coder-side outer-call initialization)
  * modify ``_searcher.py`` (T5 — FD7 lift + SkillLibrary threading)
  * implement Layer 1-4 token enforcement against the provider (T3)

The ContextVar binding mirrors the
:func:`persistence.effect.runtime.with_runtime` pattern (set/Token/reset)
which is the existing in-tree precedent for ContextVar-scoped runtime
state.

Forced spec deviations vs T1 spec:

  * **FD-T1.1 — RecursionBudget field naming.** The design doc § LD1
    references the three limits as both the ``MAX_*`` module constants
    AND as ``ctx.budget.MAX_LLM_CALL_DEPTH`` style attributes. The T1
    spec gave a CHOICE between short-form lowercase
    (``max_depth``/``max_tokens``/``max_requests``) and uppercase mirroring
    the constants. Picked the short-form lowercase to match dataclass
    conventions; the short→long mapping is documented in the dataclass
    docstring.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
from typing import Iterator

from persistence.plan._mcts import _PlanCycleDetected


# ---------------------------------------------------------------------------
# Module-level resource limits (LD1)
# ---------------------------------------------------------------------------


# Hard cap on currently-active :llm/call frames including the one about
# to start. Demo default per design § LD1; 2.4a dogfood may calibrate.
MAX_LLM_CALL_DEPTH: int = 3

# Soft cap on cumulative tokens across the full nested call tree.
# Enforced via Layer 1-4 token accounting in T3 (this T1 module only
# carries the limit; no per-call accounting here).
MAX_RECURSIVE_TOKENS: int = 20_000

# Soft cap on cumulative ``:llm/call`` invocations across the full
# nested call tree. Cumulative — NOT decremented on exit_call.
MAX_RECURSIVE_REQUESTS: int = 10


# ---------------------------------------------------------------------------
# RecursionBudget — frozen value type for budget limits (LD1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecursionBudget:
    """Frozen value type holding the three recursion limits (LD1).

    Default-constructed instance maps to the module-level constants:

      ``max_depth``    -> :data:`MAX_LLM_CALL_DEPTH`     (default ``3``)
      ``max_tokens``   -> :data:`MAX_RECURSIVE_TOKENS`   (default ``20_000``)
      ``max_requests`` -> :data:`MAX_RECURSIVE_REQUESTS` (default ``10``)

    Override at construction for tests + 2.4a dogfood tuning. Frozen so
    it's safe to share across nested ContextVar frames without aliasing
    surprises.
    """

    max_depth: int = MAX_LLM_CALL_DEPTH
    max_tokens: int = MAX_RECURSIVE_TOKENS
    max_requests: int = MAX_RECURSIVE_REQUESTS


# ---------------------------------------------------------------------------
# DispatcherContext — mutable per-dispatch state (LD1)
# ---------------------------------------------------------------------------


@dataclass(frozen=False)
class DispatcherContext:
    """Mutable per-dispatch context threaded through nested ``:llm/call``\\s.

    Lifetime spans the FULL outer dispatch/decision (one ``coder.run()``
    cycle); persists across non-``:llm/call`` ops (``:fs/read`` /
    ``:shell/exec`` / ``:git/diff`` / etc.) interleaved between calls;
    reset between coder iterations.

    Field semantics:

      * ``depth`` — count of currently-active ``:llm/call`` frames
        INCLUDING the call about to start (post-increment semantics; see
        :func:`enter_call`). Initial state ``0`` means "no active call".
        Outermost call increments to ``1`` on entry; first nested to
        ``2``; with ``MAX=3`` the allowed depths are ``{1,2,3}``.
      * ``token_count`` — cumulative tokens across the recursion tree.
        Populated by Layer 4 post-call hard accounting in T3. NOT
        decremented on exit.
      * ``request_count`` — cumulative ``:llm/call`` invocations across
        the recursion tree. Incremented in :func:`enter_call`. NOT
        decremented on exit.
      * ``cycle_path`` — content-hash-keyed ACTIVE-PATH STACK (LIST,
        not set) per LD2. Push on entry to a skill body; pop on exit.
        Cycle check raises :class:`SkillCycleDetected` if hash already
        present in active path.
      * ``parent_audit_entry_id`` — id of the OUTER call's AuditEntry
        when emitting nested-call audit entries. ``None`` at the
        outermost frame. T3 audit middleware reads/writes this.
      * ``budget`` — frozen :class:`RecursionBudget` carrying the three
        limits. Default = module constants.
    """

    depth: int = 0
    token_count: int = 0
    request_count: int = 0
    cycle_path: list[str] = field(default_factory=list)
    parent_audit_entry_id: str | None = None
    budget: RecursionBudget = field(default_factory=RecursionBudget)


# ---------------------------------------------------------------------------
# LLMRecursionBudgetExceeded — exception type
# ---------------------------------------------------------------------------


_VALID_BUDGET_FIELDS: frozenset[str] = frozenset({"depth", "tokens", "requests"})


class LLMRecursionBudgetExceeded(Exception):
    """Raised when a recursion-budget limit is exceeded (LD1).

    Attributes:
        field: which limit was exceeded — one of ``"depth" | "tokens" |
            "requests"``. Validated at construction; bogus values raise
            :class:`ValueError`.
        limit: the threshold value that was exceeded.
        observed: the actual value at the moment of the check.

    The three-attribute shape (``field`` + ``limit`` + ``observed``)
    gives downstream audit + replay tooling enough information to
    classify the failure without parsing the message string.
    """

    def __init__(self, *, field: str, limit: int, observed: int) -> None:
        if field not in _VALID_BUDGET_FIELDS:
            raise ValueError(
                f"LLMRecursionBudgetExceeded.field must be one of "
                f"{sorted(_VALID_BUDGET_FIELDS)!r}, got {field!r}"
            )
        self.field = field
        self.limit = limit
        self.observed = observed
        super().__init__(
            f"recursion budget exceeded: field={field!r}, "
            f"limit={limit}, observed={observed}"
        )

    def __str__(self) -> str:  # pragma: no cover — exercised via assertions
        return (
            f"recursion budget exceeded: field={self.field!r}, "
            f"limit={self.limit}, observed={self.observed}"
        )


# ---------------------------------------------------------------------------
# SkillCycleDetected — narrower runtime-layer error (LD2)
# ---------------------------------------------------------------------------


class SkillCycleDetected(_PlanCycleDetected):
    """Raised when execution-time dynamic active-path detects a cycle (LD2).

    Subclass of :class:`persistence.plan._mcts._PlanCycleDetected` so the
    existing ``except (ValueError, IndexError, PlanDepthExceeded)`` catch
    in the search loop covers it. Adds NO new behavior — the subclass
    exists purely for clearer error attribution between Layer A
    search-time STATIC subtree check (raises bare ``_PlanCycleDetected``)
    and Layer B execution-time DYNAMIC active-path check (raises this
    subclass).
    """


# ---------------------------------------------------------------------------
# ContextVar binding — mirrors persistence.effect.runtime.with_runtime
# ---------------------------------------------------------------------------


_DISPATCHER_CONTEXT: contextvars.ContextVar[DispatcherContext | None] = (
    contextvars.ContextVar(
        "persistence_coder_dispatcher_context", default=None
    )
)


def current_dispatcher_context() -> DispatcherContext | None:
    """Return the currently-bound :class:`DispatcherContext`, or ``None``.

    Outside any :func:`dispatcher_context` block, returns ``None``. The
    audit middleware (T3) uses this to discover whether a ``:llm/call``
    is happening inside a coder dispatch (in which case it threads
    parent_audit_entry_id) or stand-alone (no parent).
    """
    return _DISPATCHER_CONTEXT.get()


@contextlib.contextmanager
def dispatcher_context(ctx: DispatcherContext) -> Iterator[DispatcherContext]:
    """Bind ``ctx`` as the active :class:`DispatcherContext` for the block.

    On entry: ``_DISPATCHER_CONTEXT.set(ctx)`` returns a Token. On exit
    (whether the block returns normally OR raises), the Token is reset
    via ``_DISPATCHER_CONTEXT.reset(token)``. Nested invocations correctly
    restore the outer ctx on inner exit (standard ContextVar Token
    semantics).

    Mirrors :func:`persistence.effect.runtime.with_runtime` — the
    in-tree precedent for ContextVar-scoped runtime state.
    """
    token = _DISPATCHER_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _DISPATCHER_CONTEXT.reset(token)


# ---------------------------------------------------------------------------
# enter_call / exit_call — bounds-check helpers (LD1)
# ---------------------------------------------------------------------------


def enter_call(ctx: DispatcherContext) -> None:
    """Mark entry to a new ``:llm/call`` frame (LD1 post-increment semantics).

    Sequence:

      1. Increment ``ctx.depth`` FIRST.
      2. Check ``ctx.depth > ctx.budget.max_depth``; raise
         :class:`LLMRecursionBudgetExceeded(field="depth", ...)` if so
         (``>`` strict — equality is allowed; with ``max_depth=3`` the
         allowed depths are ``{1,2,3}``).
      3. Increment ``ctx.request_count``.
      4. Check ``ctx.request_count > ctx.budget.max_requests``; raise
         :class:`LLMRecursionBudgetExceeded(field="requests", ...)` if so.

    On raise, the depth + request counters are NOT rolled back — caller
    (T3 audit middleware) is responsible for surfacing the error to the
    coder loop without :func:`exit_call` being reached. Token enforcement
    (Layer 1-4) lives in T3.
    """
    ctx.depth += 1
    if ctx.depth > ctx.budget.max_depth:
        raise LLMRecursionBudgetExceeded(
            field="depth",
            limit=ctx.budget.max_depth,
            observed=ctx.depth,
        )
    ctx.request_count += 1
    if ctx.request_count > ctx.budget.max_requests:
        raise LLMRecursionBudgetExceeded(
            field="requests",
            limit=ctx.budget.max_requests,
            observed=ctx.request_count,
        )


def exit_call(ctx: DispatcherContext) -> None:
    """Mark exit from a ``:llm/call`` frame (LD1 — depth decrements only).

    Decrements ``ctx.depth``. ``ctx.request_count`` is cumulative across
    the recursion tree and is NOT decremented (LD1) — N requests across
    the tree always count toward the request budget regardless of nesting
    structure.

    ``ctx.token_count`` is also cumulative and is mutated by T3 Layer 4
    post-call hard accounting; not touched here.
    """
    ctx.depth -= 1


# ---------------------------------------------------------------------------
# cycle_path push/pop — LD2 Layer B execution-time API surface (T3 add)
# ---------------------------------------------------------------------------


def push_cycle(ctx: DispatcherContext, content_hash: str) -> None:
    """Push ``content_hash`` onto ``ctx.cycle_path`` (LD2 Layer B entry).

    Raises :class:`SkillCycleDetected` if the hash is ALREADY in
    ``cycle_path`` (the hash is currently active up the call chain
    — re-entering it would form a runtime cycle).

    The hash KEY is the FULL ``plan.id`` (32-hex chars) per LD2 R0-fold
    B3 — content-addressed registry can have aliases, so skill_id-only
    keying would miss legitimate cycle cases.

    T3 ships the API surface here; T4 wires the actual call sites
    (coder-side, when a grafted plan body's ``:llm/call`` leaf is
    dispatched). The audit-stack middleware does NOT push/pop because it
    doesn't know which skill body the ``:llm/call`` originated from at
    that layer.
    """
    if content_hash in ctx.cycle_path:
        raise SkillCycleDetected(
            f"skill cycle detected: content_hash {content_hash!r} "
            f"is already active in cycle_path {ctx.cycle_path!r}"
        )
    ctx.cycle_path.append(content_hash)


def pop_cycle(ctx: DispatcherContext, content_hash: str) -> None:
    """Pop the matching ``content_hash`` from ``ctx.cycle_path`` (LD2 Layer B exit).

    LIFO discipline — the top-of-stack MUST equal the popped hash.
    Mismatch raises :class:`RuntimeError` because it indicates a caller
    bug in the push/pop pairing (T4 must keep the pairs balanced even
    across exception paths via try/finally).
    """
    if not ctx.cycle_path or ctx.cycle_path[-1] != content_hash:
        top = ctx.cycle_path[-1] if ctx.cycle_path else None
        raise RuntimeError(
            f"cycle_path stack discipline violated: top={top!r} "
            f"pop={content_hash!r}"
        )
    ctx.cycle_path.pop()


__all__ = [
    "DispatcherContext",
    "LLMRecursionBudgetExceeded",
    "MAX_LLM_CALL_DEPTH",
    "MAX_RECURSIVE_REQUESTS",
    "MAX_RECURSIVE_TOKENS",
    "RecursionBudget",
    "SkillCycleDetected",
    "current_dispatcher_context",
    "dispatcher_context",
    "enter_call",
    "exit_call",
    "push_cycle",
    "pop_cycle",
]
