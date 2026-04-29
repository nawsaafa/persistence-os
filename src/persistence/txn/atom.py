"""Atom — single-cell CAS over datom-refs.

Atoms are the "I want CAS without a transaction" ergonomic fast-path
Clojure ``Atom.java`` ships. They write under fixed ``"value"`` attribute
on a single eid, using the same Store writer-lock that
``_commit_attempt`` uses for its check+transact gate
(``transaction.py:325``). No new public substrate API — atoms reach
into ``db.store._lock`` exactly the way ``_commit_attempt`` does, both
inside the txn module.

Intentional Clojure-parity deviation: atom ops invoked under the
``_DOSYNC_GUARD`` ContextVar raise ``AtomInDosyncProhibited`` rather
than running immediately as Clojure does. Rationale: persistence-os's
``dosync`` body is the intent-queue + retry domain; allowing atom CAS
inside would punch a non-replayable hole in the audit chain (atom
writes wouldn't appear in commit-id provenance). See design doc § F1
"Intentional Clojure-parity deviation" block. The user's escape hatch
for transactional reads is ``tx.deref(ref)`` (refs); for transactional
writes, ``tx.assoc`` / ``tx.alter``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from persistence.txn.errors import AtomCASExhausted, AtomInDosyncProhibited
from persistence.txn.intents import is_in_dosync


# The fixed attribute under which atom values live on the Datom log.
# Matches the default ``Ref.spec_attr`` so atoms and refs are observable
# the same way at the entity level (``view.entity(eid)["value"]``).
# Stored bare — the Datom store strips a leading ``":"`` at construction
# (``datom.py:174``), so ``"value"`` and ``":value"`` would canonicalise
# to the same wire form anyway. We use bare to match the existing
# convention in ``_build_write_facts`` (``transaction.py:200``).
_ATOM_ATTR = "value"


def _check_not_in_dosync() -> None:
    """Raise ``AtomInDosyncProhibited`` if called inside an active dosync.

    Applied to ALL atom ops (deref, swap, compare_and_set, reset) so the
    rule is consistent. Inside a dosync body, the user must use
    ``tx.deref`` / ``tx.assoc`` / ``tx.alter`` — these are the audit-
    chain-replayable surface.
    """
    if is_in_dosync():
        raise AtomInDosyncProhibited(
            "atom operations are prohibited inside a dosync body. "
            "Use tx.deref(ref) / tx.assoc(ref, v) / tx.alter(ref, fn) "
            "for the in-transaction read/write surface; atoms exist for "
            "the transaction-free CAS case."
        )


# Defensive cap on the swap retry loop. Under the writer-lock idiom
# (lock held across read+transact) the loop ALWAYS succeeds on first
# attempt — there is no contention while the lock is held. This bound
# exists as a backstop for hypothetical future lock-free fast paths and
# is mirrored from ``transaction.DEFAULT_MAX_RETRIES`` for parity with
# ``dosync``.
_DEFAULT_ATOM_MAX_RETRIES = 256


@dataclass(frozen=True, slots=True)
class Atom:
    """Immutable handle to a single-cell CAS atom.

    Constructed via :meth:`persistence.fact.db.DB.atom`. Two atoms
    naming the same ``(eid, db_id)`` compare equal regardless of
    construction site — the back-reference to the DB (``_db``) is
    deliberately excluded from eq/hash via ``compare=False`` so identity
    is stable across re-fetches.

    API mirrors Clojure's ``Atom.java``:

    - :meth:`deref` — read the latest value (or ``None`` if unwritten).
    - :meth:`swap` — read-modify-write loop with CAS retry.
    - :meth:`compare_and_set` — single-shot guard.
    - :meth:`reset` — unconditional write.

    All four ops raise :class:`AtomInDosyncProhibited` if invoked inside
    a ``dosync`` body — see module docstring for rationale.
    """

    eid: str
    db_id: str
    # Private back-reference to the DB. Excluded from eq/hash/repr so
    # two Atoms to the same (eid, db_id) pair compare equal regardless
    # of construction site (mirrors Ref's spec_attr exclusion). The
    # leading underscore signals "do not touch directly"; public API
    # is the four methods below.
    _db: Any = field(compare=False, repr=False, default=None)

    def __repr__(self) -> str:
        return f"Atom({self.eid!r}, db={self.db_id!r})"

    # ─── reads ───────────────────────────────────────────────────────────

    def _read_latest_value(self) -> Any:
        """Return the latest open ``:value`` assert for this eid, or ``None``.

        Walks the log newest-to-oldest looking for the most recent
        non-invalidated, open-interval assert under
        ``(self.eid, _ATOM_ATTR)``. Mirrors the
        ``_find_prior_assert`` semantics used by ``db.transact``'s
        auto-retraction path.

        Caller is responsible for holding ``db.store._lock`` if the
        read needs to be linearised against concurrent writes
        (``swap`` / ``compare_and_set`` do; ``deref`` reads the tip
        eventually-consistently).
        """
        # ``self._db.log()`` returns an Iterator; we must materialise to
        # iterate in reverse cheaply. The log is also the cheapest path
        # — building an entity view costs O(N) too and we don't need
        # the full projection, just the last open assert.
        for d in reversed(list(self._db.log())):
            if d.e != self.eid or d.a != _ATOM_ATTR:
                continue
            if d.op != "assert":
                continue
            if d.invalidated_by is not None:
                continue
            if d.valid_to is not None:
                continue
            return d.v
        return None

    def deref(self) -> Any:
        """Read the latest value of this atom, or ``None`` if never written.

        Single-datom log read. No ``dosync`` required — but raises
        ``AtomInDosyncProhibited`` if called inside one (use
        ``tx.deref(ref)`` for in-txn reads instead).
        """
        _check_not_in_dosync()
        return self._read_latest_value()

    # ─── writes ──────────────────────────────────────────────────────────

    def _transact_value(self, value: Any) -> None:
        """Write ``value`` to this atom's eid under the fixed atom attr.

        Lock acquisition is the caller's responsibility. ``db.transact``
        will internally re-acquire ``store._lock`` (it's an RLock since
        v0.5.0a1 B7) so this is safe under either held-lock or no-lock
        callsites; we keep the ergonomic responsibility with the caller
        so the read+write window stays under one lock.
        """
        self._db.transact([{
            "e": self.eid,
            "a": _ATOM_ATTR,
            "v": value,
            "valid_from": self._db._clock(),
        }])

    def swap(
        self,
        fn: Callable[[Any], Any],
        *,
        max_retries: int = _DEFAULT_ATOM_MAX_RETRIES,
    ) -> Any:
        """Read-modify-write loop. Apply ``fn(current)`` and write the result.

        Returns the new value on success.

        CAS implementation mirrors ``_commit_attempt`` at
        ``transaction.py:325`` — acquire ``db.store._lock``, read the
        tip, compute, write, all under one lock window. Because the
        lock is held across read+transact there is no interleave window
        — the loop always succeeds on first attempt. The retry budget
        is a defensive backstop against future lock-free fast paths;
        if it ever exhausts under the current implementation, that's a
        substrate bug, not a contention case.

        The ``fn`` callback runs INSIDE the lock window. Keep it short
        and pure; long-running or blocking work inside ``fn`` will
        starve other writers.

        Raises :class:`AtomInDosyncProhibited` if called inside a
        ``dosync`` body. Raises :class:`AtomCASExhausted` if
        ``max_retries`` is exceeded (defensive — should not fire under
        the writer-lock idiom).
        """
        _check_not_in_dosync()
        for _attempt in range(max_retries):
            with self._db.store._lock:
                current = self._read_latest_value()
                new_value = fn(current)
                # Lock held — no interleave possible between the read
                # and this write. Explicit re-read isn't necessary here
                # (the lock guarantees linearisation) but documenting
                # the property in the comment keeps the intent grep-
                # able and matches the design doc § F1 pseudocode.
                self._transact_value(new_value)
                return new_value
        raise AtomCASExhausted(
            f"Atom.swap exhausted max_retries={max_retries} on eid "
            f"{self.eid!r}. Under the writer-lock idiom this is a "
            f"substrate bug — please file an issue."
        )

    def compare_and_set(self, old: Any, new: Any) -> bool:
        """Single-shot guard. Write ``new`` iff current value equals ``old``.

        Returns ``True`` on success, ``False`` if the current value
        differs (no write performed). Raises
        :class:`AtomInDosyncProhibited` if called inside a ``dosync``
        body.
        """
        _check_not_in_dosync()
        with self._db.store._lock:
            current = self._read_latest_value()
            if current != old:
                return False
            self._transact_value(new)
            return True

    def reset(self, value: Any) -> Any:
        """Unconditional write. Always overwrites the current value.

        Returns the new value (the ``value`` argument, for chaining).
        Raises :class:`AtomInDosyncProhibited` if called inside a
        ``dosync`` body.
        """
        _check_not_in_dosync()
        with self._db.store._lock:
            self._transact_value(value)
        return value


__all__ = ["Atom"]
