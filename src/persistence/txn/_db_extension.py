"""Attach txn methods (db.ref, db.new_ref, db.dosync) to fact.db.DB.

This is the single inbound coupling-point from txn → fact. The DB class
itself stays in fact/; this module monkey-patches the four new methods
onto it at module-import time.

Phase A ships ``ref`` and ``new_ref``. Phase B adds ``dosync`` and
``dosync_decorator``.
"""
from __future__ import annotations

import contextlib
import functools
import uuid
from typing import Any, Callable, Optional

from persistence.txn.atom import Atom
from persistence.txn.errors import RefValueNotImmutable
from persistence.txn.ref import Ref, is_immutable_value

# Per-DB-instance id used to bind Refs to the DB they were constructed
# against. Stored on the DB via setattr; computed lazily on first access.
_DB_ID_ATTR = "_persistence_txn_db_id"


def _get_db_id(db: Any) -> str:
    db_id = getattr(db, _DB_ID_ATTR, None)
    if db_id is None:
        # Use the python id() of the DB object as the basis. This is
        # process-local and stable for the lifetime of the DB; sufficient
        # for ref-branch-mismatch detection within one process.
        db_id = f"db-{id(db):x}"
        # DB is a regular @dataclass and accepts setattr. If a future
        # variant is frozen/slotted this will raise AttributeError; the
        # fix at that point is a WeakKeyDictionary side table.
        setattr(db, _DB_ID_ATTR, db_id)
    return db_id


def _ref(self: Any, eid: str, *, spec_attr: str = "value") -> Ref:
    """DB.ref(eid, *, spec_attr="value") — handle to existing/future entity by id.

    ``spec_attr`` (v0.5.1, keyword-only) names which write-spec the ref's
    writes are validated against AND which entity-attribute name the
    value lives under. Default ``"value"`` preserves v0.5.0a1 behavior
    bit-for-bit. See ``Ref`` docstring for the eq/hash invariant.
    """
    if not isinstance(eid, str):
        raise TypeError(f"eid must be str, got {type(eid).__name__}")
    return Ref(eid=eid, db_id=_get_db_id(self), spec_attr=spec_attr)


def _new_ref(
    self: Any,
    initial: Optional[Any] = None,
    *,
    spec_attr: str = "value",
) -> Ref:
    """DB.new_ref(initial=..., *, spec_attr="value") — allocate a fresh UUID4 entity-id.

    The ``initial`` parameter is validated as immutable and then
    DISCARDED in v0.5.0a1 — the Ref returned does NOT carry the
    initial value forward. To set a starting value, follow up with
    ``tx.assoc(ref, value)`` inside a dosync (Phase B).

    The parameter is accepted now so callers can write API-stable code
    against the Phase A surface; a future revision may persist it
    through a side table without breaking signatures.

    Accepted ``initial`` types (when provided): pyrsistent.PMap /
    PVector / PSet, frozen built-in, or frozen dataclass instance.

    ``spec_attr`` (v0.5.1, keyword-only) — see ``DB.ref`` for semantics.
    """
    if initial is not None and not is_immutable_value(initial):
        raise RefValueNotImmutable(
            f"db.new_ref(initial=...) value must be immutable; got "
            f"{type(initial).__name__!r}. Use persistence.txn.freeze(...) "
            f"or wrap in pyrsistent.pmap/pvector/pset."
        )
    eid = str(uuid.uuid4())  # uuid4; uuid7 not in stdlib until 3.13+  # noqa: wall-clock
    return Ref(eid=eid, db_id=_get_db_id(self), spec_attr=spec_attr)



class _DosyncHandle:
    """Returned by ``db.dosync(max_retries=..., deadline=...)`` (parenthesized form).

    Acts as BOTH a context manager (``with db.dosync(...) as tx:``) AND a
    decorator factory (``@db.dosync(...)``), matching Clojure's dosync
    dual-mode idiom.  Without this, ``@db.dosync(max_retries=0)`` would
    receive a contextlib._GeneratorContextManager object as the decorated
    function, which is not callable as a decorator.
    """

    def __init__(self, db: Any, max_retries: int, deadline: Any) -> None:
        self._db = db
        self._max_retries = max_retries
        self._deadline = deadline
        self._cm: Any = None  # lazily built on __enter__

    # ── decorator-factory usage: @db.dosync(max_retries=N) def f(tx): ...
    def __call__(self, func: Any) -> Any:
        from persistence.txn.transaction import _run

        @functools.wraps(func)
        def runner(*args: Any, **kwargs: Any) -> Any:
            def with_tx(tx: Any) -> Any:
                return func(tx, *args, **kwargs)
            return _run(
                self._db, with_tx,
                max_retries=self._max_retries,
                deadline=self._deadline,
            )
        return runner

    # ── context-manager usage: with db.dosync(deadline=T) as tx: ...
    def __enter__(self) -> Any:
        self._cm = _build_dosync_cm(self._db)
        return self._cm.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._cm.__exit__(*exc)


def _build_dosync_cm(db: Any) -> Any:
    """Build the contextmanager object for the CM form of dosync."""
    @contextlib.contextmanager
    def cm() -> Any:
        from persistence.txn.transaction import Transaction, _commit_attempt
        from persistence.txn.intents import set_dosync_guard, clear_dosync_guard, is_in_dosync
        from persistence.txn.errors import NestedDosyncNotSupported, TxnRetryExhausted

        if is_in_dosync():
            raise NestedDosyncNotSupported(
                "nested dosync is not supported in v0.5.0a1"
            )
        t_start = db._clock()
        tx = Transaction(db=db, t_start=t_start, attempt=0)
        token = set_dosync_guard()
        try:
            yield tx
        finally:
            clear_dosync_guard(token)
        # Commit path (only reached on clean exit; exceptions from
        # the with-block body skip this). The CM form is single-shot:
        # a False return from _commit_attempt is escalated to
        # TxnRetryExhausted instead of looping.
        if not _commit_attempt(tx):
            raise TxnRetryExhausted(
                "dosync context-manager detected conflict on commit; "
                "use the @db.dosync decorator form for retry-on-conflict"
            )

    return cm()


def _dosync(
    self: Any,
    body_or_none: Optional[Callable] = None,
    *,
    max_retries: Optional[int] = None,
    deadline: Optional[float] = None,
) -> Any:
    """DB.dosync — both context-manager and decorator forms.

    Usage:
        with db.dosync() as tx: ...
        with db.dosync(deadline=5.0) as tx: ...
        with db.dosync(max_retries=512) as tx: ...

        @db.dosync
        def transfer(tx): ...

        @db.dosync(deadline=2.0, max_retries=512)
        def transfer(tx): ...

    Retry semantics — IMPORTANT asymmetry:
        - The CM form (``with db.dosync(...) as tx:``) is single-shot:
          on a conflict at commit time it raises ``TxnRetryExhausted``
          immediately. Suitable for one-off scripted writes where the
          caller wants to handle conflicts explicitly.
        - The decorator form (``@db.dosync`` / ``@db.dosync(...)``) is
          the canonical retryable form: it re-runs the body up to
          ``max_retries`` times (default 256) on conflict.
    """
    from persistence.txn.transaction import _run, DEFAULT_MAX_RETRIES

    effective_max_retries = (
        max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
    )

    # Bare-decorator form: @db.dosync def f(tx): ...
    if callable(body_or_none) and max_retries is None and deadline is None:
        body = body_or_none

        @functools.wraps(body)
        def runner(*args: Any, **kwargs: Any) -> Any:
            def with_tx(tx: Any) -> Any:
                return body(tx, *args, **kwargs)
            return _run(self, with_tx, max_retries=effective_max_retries, deadline=deadline)
        return runner

    # Parenthesized form: db.dosync() / db.dosync(max_retries=N, deadline=T).
    # When used as @db.dosync(...), Python applies the result as a decorator,
    # so _DosyncHandle.__call__ fires.  When used as `with db.dosync(...) as tx:`,
    # _DosyncHandle.__enter__/__exit__ fire.  Both paths are correct.
    if body_or_none is None:
        return _DosyncHandle(self, effective_max_retries, deadline)

    raise RuntimeError("unreachable: dosync invocation pattern not recognised")

def _atom(self: Any, eid: str, *, initial: Any) -> Atom:
    """DB.atom(eid, *, initial) — construct a fresh single-cell CAS atom.

    Atoms are the "I want CAS without a transaction" ergonomic fast-path
    Clojure ``Atom.java`` ships. ``initial`` is written immediately under
    the fixed atom attribute (``"value"``) on ``eid`` and returned as the
    atom's starting state.

    Raises ``ValueError`` if ``eid`` already has a ``:value`` datom — the
    constructor is for fresh allocation only. Use :meth:`Atom.reset` if
    you want to overwrite an existing atom's value.

    The pre-existence check uses the same log-walk as
    ``Atom._read_latest_value``: looks for any non-invalidated, open-
    interval ``"value"`` assert under ``eid``.
    """
    if not isinstance(eid, str):
        raise TypeError(f"eid must be str, got {type(eid).__name__}")
    # v0.5.2 R2 W1 (MAJOR-1 closure): pre-existence scan AND initial
    # write happen under one ``store._lock`` window so two concurrent
    # ``db.atom(eid, ...)`` calls on the same eid linearise — one wins
    # with the initial value, the other raises ``ValueError``. Without
    # the spanning lock the scan can pass on both threads before either
    # writes, then both transact and we silently allocate two "fresh"
    # atoms over the same eid. ``store._lock`` is an RLock so the
    # nested ``self.transact(...)`` re-acquire in ``allocate_and_append``
    # is safe (matches ``_commit_attempt`` at ``transaction.py:325``).
    with self.store._lock:
        # Pre-existence guard. Walks the log once; for fresh DBs and small
        # logs this is O(N) but cheap. The same scan ``Atom._read_latest_value``
        # uses, but we don't need to instantiate the atom yet — keeps the
        # error path on ValueError instead of constructing+probing.
        for d in self.log():
            if d.e != eid or d.a != "value":
                continue
            if d.op != "assert":
                continue
            if d.invalidated_by is not None:
                continue
            if d.valid_to is not None:
                continue
            raise ValueError(
                f"db.atom: eid {eid!r} already has an open ``:value`` datom; "
                f"use atom.reset(...) to overwrite an existing atom's value."
            )

        atom = Atom(eid=eid, db_id=_get_db_id(self), _db=self)
        # Write the initial value via the same path swap/reset use.
        # Lock held across check+write linearises concurrent allocators.
        self.transact([{
            "e": eid,
            "a": "value",
            "v": initial,
            "valid_from": self._clock(),
        }])
    return atom


def _attach_txn_methods(db_cls: type) -> None:
    """Attach the txn DB-level methods (``ref``, ``new_ref``, ``dosync``,
    ``atom``) to ``db_cls``.

    Re-entrant safe — repeated calls overwrite the attributes with the
    same module-level functions, so the result is unchanged. Test code
    may reset via ``del db_cls.ref`` to force re-attach.
    """
    db_cls.ref = _ref            # type: ignore[attr-defined]
    db_cls.new_ref = _new_ref    # type: ignore[attr-defined]
    db_cls.dosync = _dosync      # type: ignore[attr-defined]
    db_cls.atom = _atom          # type: ignore[attr-defined]


__all__ = ["_attach_txn_methods"]
