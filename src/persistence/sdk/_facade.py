"""``Substrate`` — curated namespace + lifecycle facade (SDK2 body).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-1, :class:`Substrate` is a thin curated namespace over the seven
Persistence OS modules — NOT a god-object wrapper.

The body lands in SDK2 (this slice). SDK1 shipped an empty placeholder so
that ``from persistence.sdk import Substrate`` was importable from day
one; SDK2 fills in:

- ``Substrate.open(uri)`` classmethod that drives URI dispatch via
  :func:`persistence.sdk.uri.open_store`,
- idempotent ``close()``,
- ``__enter__`` / ``__exit__`` context-manager support,
- six **curated** subsurface namespaces (``fact`` / ``effect`` / ``txn``
  / ``repl`` / ``audit`` / ``replay``) that thin-pass-through to the
  underlying Module surfaces — the curated shape replaces the SDK1-design
  "raw Module reach-through" interpretation per ADR-1's "curated
  namespace, not raw module" line,
- one **escape-hatch** namespace ``escape`` that reaches into the raw
  Module instances (`s.escape.fact` etc.) and emits a
  ``:sdk/escape-hatch-access`` audit entry on first access per-session,
- promotion of the class stability marker from
  ``@experimental`` (SDK1 placeholder) to ``@stable("v0.8")``.

Adapter authors are documented to bind to the curated namespaces and to
treat any reach-through into ``s.escape.*`` as out-of-contract per the
ADR-1 escape-hatch boundary.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from persistence.sdk._audit import build_escape_hatch_payload
from persistence.sdk._stability import experimental, stable
from persistence.sdk.uri import open_store


# ---------------------------------------------------------------------------
# Curated subsurface namespaces
# ---------------------------------------------------------------------------
# Each namespace is a small bound-method facade — adapter authors call
# ``s.fact.transact(...)`` etc., and each call thin-pass-throughs to the
# real impl on the underlying ``DB`` / ``Runtime`` / etc. Per ADR-1 these
# namespaces are the contract surface; the raw Module instances live
# behind ``s.escape.<module>`` so that reach-through is observable and
# audit-emitting.
#
# The namespaces are intentionally THIN — they expose the load-bearing
# methods listed in the design doc § 4 (the G1 gate exercises the
# canonical ones) without becoming god-objects. v0.9 may add curated
# methods that proved load-bearing during Phase-2 dogfooding; v0.8 keeps
# the surface deliberately small.


class _FactNamespace:
    """Curated ``s.fact.*`` surface.

    Thin pass-through to :class:`persistence.fact.DB`. The substrate's
    underlying ``DB`` instance is reachable via ``s.escape.fact`` for
    callers that need methods not yet folded into this curated namespace
    (per ADR-1 W2 escape-hatch telemetry).
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def transact(self, datoms, **kwargs):
        """Append datoms to the bitemporal log; thin pass-through to
        :meth:`persistence.fact.DB.transact`.
        """
        return self._substrate._db.transact(datoms, **kwargs)

    def transact_batch(self, batches, **kwargs):
        """Multi-batch append; thin pass-through to
        :meth:`persistence.fact.DB.transact_batch`.
        """
        return self._substrate._db.transact_batch(batches, **kwargs)

    def as_of(self, t):
        """Snapshot view at transaction time ``t``; thin pass-through to
        :meth:`persistence.fact.DB.as_of`.
        """
        return self._substrate._db.as_of(t)

    def as_of_valid(self, t):
        """Snapshot view at valid time ``t``; thin pass-through to
        :meth:`persistence.fact.DB.as_of_valid`.
        """
        return self._substrate._db.as_of_valid(t)

    def history(self, e, a):
        """Time-ordered history for entity-attribute pair; thin
        pass-through to :meth:`persistence.fact.DB.history`.
        """
        return self._substrate._db.history(e, a)

    def since(self, t):
        """Datoms transacted since ``t``; thin pass-through to
        :meth:`persistence.fact.DB.since`.
        """
        return self._substrate._db.since(t)


class _EffectNamespace:
    """Curated ``s.effect.*`` surface.

    Thin pass-through to :class:`persistence.effect.Runtime`. Callers
    needing the raw runtime (e.g. to push handlers) reach via
    ``s.escape.effect``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def perform(self, op, args=None, **kwargs):
        """Dispatch an effect through the runtime stack; thin
        pass-through to :meth:`persistence.effect.Runtime.perform`.
        """
        if args is None:
            args = {}
        return self._substrate._runtime.perform(op, args, **kwargs)

    def is_well_formed(self, catalog) -> bool:
        """Check the runtime stack covers every op in ``catalog``;
        thin pass-through to
        :meth:`persistence.effect.Runtime.is_well_formed`.
        """
        return self._substrate._runtime.is_well_formed(catalog)


class _TxnNamespace:
    """Curated ``s.txn.*`` surface.

    Thin pass-through to the dosync-attached ``DB`` from
    :mod:`persistence.txn`. The :func:`dosync` entry-point in this
    namespace mirrors the ``db.dosync`` attached method signature so
    adapter authors can write ``with s.txn.dosync() as tx: ...``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def dosync(self, *args, **kwargs):
        """Atomic dosync block; thin pass-through to
        :meth:`persistence.fact.DB.dosync` (attached by
        :mod:`persistence.txn._db_extension`).

        Both context-manager and decorator forms are forwarded:

        - ``with s.txn.dosync() as tx: ...`` — context manager
        - ``@s.txn.dosync`` / ``@s.txn.dosync(deadline=2.0)`` — decorator
        """
        return self._substrate._db.dosync(*args, **kwargs)

    def new_ref(self, value, **kwargs):
        """Allocate a fresh :class:`persistence.txn.Ref`; thin
        pass-through to :meth:`persistence.fact.DB.new_ref` (attached
        by :mod:`persistence.txn._db_extension`).
        """
        return self._substrate._db.new_ref(value, **kwargs)

    def ref(self, ref_id, **kwargs):
        """Resolve an existing :class:`persistence.txn.Ref` by id; thin
        pass-through to :meth:`persistence.fact.DB.ref`.
        """
        return self._substrate._db.ref(ref_id, **kwargs)

    @experimental(
        reason=(
            "PG6 R3-M1: speculation / rollback / checkpointing primitive. "
            "Surface may evolve in v0.9 once Phase-2 dogfooding identifies "
            "the load-bearing sub-shape; adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def fold(self, seed, items, fn, **kwargs):
        """Curated re-export of :meth:`persistence.fact.DB.fold`.

        Folds ``items`` through ``fn`` against the substrate's underlying
        :class:`DB`, accumulating fact-emission transactionally per
        checkpoint. See :meth:`persistence.fact.DB.fold` for the full
        signature, error-handling discipline (``on_error``), batch
        semantics (``checkpoint_every``), and stability promise.

        ``@experimental`` per Adapter SDK ADR-5 — this is a new surface
        that the v0.8 contract intentionally does NOT cover; the v0.9
        cycle decides whether to fold the shape into a curated stable
        namespace based on Phase-2 dogfooding signal.
        """
        return self._substrate._db.fold(seed, items, fn, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c #145: speculation/scoring/choose convenience "
            "on top of DB.fold. Surface may evolve in v0.9 once Phase-2 "
            "dogfooding identifies whether the 3-tuple `fn` shape "
            "(acc, facts, score) and the FoldBranchScore record are the "
            "load-bearing form. Adapter authors who depend on it should "
            "not pin against @stable('v0.8') semantics."
        )
    )
    def fold_into(self, seed, items, fn, choose, **kwargs):
        """Run a fold and pick a winning branch in a single dosync.

        ``s.txn.fold_into`` is a convenience method on top of
        :meth:`persistence.fact.DB.fold` that runs the fold under an
        agent-extended ``fn(acc, item, db) -> (new_acc, facts, score)``
        signature, applies the user-supplied ``choose`` callback to
        the per-branch scores, and emits a single ``:fold/chosen``
        audit datom marking the winning branch. The audit datom rides
        the existing Merkle chain at
        ``persistence.effect.handlers.audit`` — same chain as
        ``:plan/edit`` / ``:code/exec`` — so cross-trajectory audit
        consistency is preserved.

        Designed for the agent's MCTS-scoring path, where N candidate
        branches are evaluated, scored, and one is chosen. The
        non-chosen branches' fact emissions remain in the substrate as
        "considered but not chosen" — this matches Persistence OS'
        append-only ethos and allows replay to reconstruct the full
        decision context (not just the winner).

        See :func:`persistence.sdk._fold_into.fold_into` for the full
        signature, error-handling discipline (``on_error``,
        ``checkpoint_every``, ``provenance``), the dosync gate, and
        the ``FoldIntoResult`` shape.

        ``@experimental`` per Adapter SDK ADR-5 + Phase-2 ADR-7. The
        ``s.txn.fold_into`` shape may evolve in v0.9 once Phase-2
        dogfooding tells us whether the 3-tuple ``fn`` and the
        ``FoldBranchScore`` record are the load-bearing surface.
        Adapter authors should NOT pin against ``@stable("v0.8")``
        semantics; they get promoted to ``@stable("v0.9")`` after
        Phase 2 dogfood survives without API change.
        """
        from persistence.sdk._fold_into import fold_into as _fold_into

        return _fold_into(
            self._substrate._db, seed, items, fn, choose, **kwargs
        )


class _ReplNamespace:
    """Curated ``s.repl.*`` surface — REPL server FACTORY.

    Per ADR-12 the SDK does NOT auto-start the REPL server. Adapter
    authors who want a network-listening REPL session call
    :meth:`serve` explicitly. The curated namespace exposes server
    construction + the token-lifecycle helpers from
    :mod:`persistence.repl`.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def serve(self, **kwargs):
        """Construct (but do not run) a :class:`persistence.repl.WSServer`
        bound to this substrate's :class:`DB`. Adapter authors call
        ``server.serve(...)`` (the aiohttp run-loop) themselves; the
        SDK does not commit a port lifetime to the substrate.

        Per ADR-12 + design § 4: the substrate's ``s.close()`` is the
        teardown handle; if a REPL server was started, the adapter is
        responsible for its lifecycle.
        """
        from datetime import datetime, timezone

        from persistence.repl import WSServer

        # The default ``runtime_clock`` reaches the system clock as a
        # last resort — production deployments inject an effect-routed
        # clock via the kwarg. The fallback path is the deliberate
        # injection seam ``persistence.repl._ws.WSServer.__init__``
        # already exposes (its existing public-API behavior is
        # ``runtime_clock`` defaults to ``datetime.now`` in tests too).
        runtime_clock = kwargs.pop(
            "runtime_clock",
            lambda: datetime.now(tz=timezone.utc),  # noqa: wall-clock
        )
        return WSServer(
            self._substrate._db,
            runtime_clock=runtime_clock,
            **kwargs,
        )

    def mint_token(self, **kwargs):
        """Issue a fresh capability token; thin pass-through to
        :func:`persistence.repl.mint_token`.
        """
        from persistence.repl import mint_token

        return mint_token(**kwargs)

    def revoke_token(self, *args, **kwargs):
        """Revoke a token (idempotent); thin pass-through to
        :func:`persistence.repl.revoke_token`.
        """
        from persistence.repl import revoke_token

        return revoke_token(*args, **kwargs)

    def list_tokens(self, *args, **kwargs):
        """Enumerate active tokens; thin pass-through to
        :func:`persistence.repl.list_tokens`.
        """
        from persistence.repl import list_tokens

        return list_tokens(*args, **kwargs)


class _AuditNamespace:
    """Curated ``s.audit.*`` surface.

    Exposes the audit-chain integrity probe (:func:`verify_chain`) and
    read-only access to the substrate's session-local audit entry list.
    The list is the same in-memory ``entries`` parameter passed into
    :func:`make_audit_handler` at substrate-open time.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def verify_chain(self, entries=None) -> bool:
        """Re-verify the Merkle chain on a sequence of audit entries;
        defaults to the substrate's session-local entry list. Thin
        pass-through to :func:`persistence.effect.verify_chain`.
        """
        from persistence.effect import verify_chain

        if entries is None:
            entries = self._substrate._audit_entries
        return verify_chain(entries)

    def entries(self):
        """Return a snapshot tuple of the substrate's audit-entry list.

        The caller gets a tuple (immutable) so adapter code that wants
        to filter / window the chain cannot accidentally mutate the
        substrate's ledger. Order is append-order.
        """
        return tuple(self._substrate._audit_entries)


class _ReplayNamespace:
    """Curated ``s.replay.*`` surface.

    Thin pass-through to :mod:`persistence.replay`. The three load-bearing
    primitives (record / replay / compare) are surfaced; adapter authors
    needing the lower-level ``EffectHandler`` / ``Trajectory`` classes
    reach via ``s.escape.replay``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def record(self, *args, **kwargs):
        """Record a factual trajectory; thin pass-through to
        :func:`persistence.replay.record`.
        """
        from persistence.replay import record

        return record(*args, **kwargs)

    def replay(self, *args, **kwargs):
        """Re-execute a trajectory under interventions; thin pass-through
        to :func:`persistence.replay.replay`.
        """
        from persistence.replay import replay as _replay

        return _replay(*args, **kwargs)

    def compare(self, *args, **kwargs):
        """Diff two trajectories; thin pass-through to
        :func:`persistence.replay.compare`.
        """
        from persistence.replay import compare

        return compare(*args, **kwargs)


class _EscapeNamespace:
    """``s.escape.*`` — the raw-module reach-through namespace.

    Per ADR-1 + ADR-1 W2 SHOULD-FIX 3 + W3 NIT-5: the seven attributes
    here (``fact`` / ``effect`` / ``plan`` / ``replay`` / ``txn`` /
    ``spec`` / ``repl``) return the **raw** Module instance (or module
    object) — bypassing the curated namespaces above. Each first
    access per-session emits exactly one ``:sdk/escape-hatch-access``
    audit entry; subsequent accesses to the same attribute in the same
    session are silent.

    The audit-entry shape is ``@experimental`` (NOT ``@stable("v0.8")``)
    per ADR-1 W3 NIT-5; see :func:`persistence.sdk._audit.build_escape_hatch_payload`.
    """

    # Mapping from escape-hatch attr name → callable that returns the raw
    # underlying surface for the substrate. Lazy module imports keep the
    # SDK import-time graph minimal.
    _RAW_RESOLVERS: dict[str, Any] = {}

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate
        # Per-instance: which attrs have already emitted their telemetry
        # entry this session. Lookup-by-attr keeps the dedupe scope
        # exactly per ADR-1 W2 ("one entry per session per attribute").
        self._emitted: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only invoked when a normal attribute lookup
        # fails, so it doesn't shadow ``_substrate`` / ``_emitted``.
        if name in _EscapeNamespace._RAW_RESOLVERS:
            self._record_first_access(name)
            return _EscapeNamespace._RAW_RESOLVERS[name](self._substrate)
        raise AttributeError(
            f"'escape' namespace has no attribute {name!r}; valid escape-"
            f"hatch attrs: {sorted(_EscapeNamespace._RAW_RESOLVERS)!r}"
        )

    def __dir__(self) -> list[str]:
        # Surface the seven escape-hatch names so introspection tools
        # (REPL completion, IDE) can find them.
        return sorted(_EscapeNamespace._RAW_RESOLVERS) + [
            "_substrate",
            "_emitted",
        ]

    def _record_first_access(self, name: str) -> None:
        """Emit the ``:sdk/escape-hatch-access`` audit entry on first
        access per attribute. The entry is appended to the substrate's
        ``_audit_entries`` list (the same list that backs
        ``s.audit.entries()``).

        Per W3 NIT-5 the entry shape itself is not part of the v0.8
        contract; downstream tooling that parses these entries does so
        at its own risk.
        """
        if name in self._emitted:
            return
        self._emitted.add(name)
        # Build the payload via the @experimental helper. ``caller_depth``
        # of 3 puts the resolved frame at the adapter source line that
        # accessed ``s.escape.<name>`` — depth 1 is __getattr__, depth 2
        # is the bound-method that made the call inside __getattr__,
        # depth 3 is the user's adapter source. Tests may pin a smaller
        # depth via direct calls into ``build_escape_hatch_payload``.
        payload = build_escape_hatch_payload(
            module=name,
            session_id=self._substrate._session_id,
            caller_depth=3,
        )
        # Append a lightweight dict envelope. v0.8 ships these as plain
        # dicts on ``Substrate._audit_entries`` (NOT ``AuditEntry``
        # records — those carry a hash chain, and SDK2 does not yet
        # boot the audit handler stack; SDK3 wires a real handler when
        # the MCP server's audit emission lands). The dict shape is
        # documented as ``@experimental`` so SDK3's swap to
        # ``AuditEntry`` is not a contract break.
        self._substrate._audit_entries.append(
            {"op": ":sdk/escape-hatch-access", "args": payload}
        )


def _resolve_raw_fact(s: "Substrate") -> Any:
    return s._db


def _resolve_raw_effect(s: "Substrate") -> Any:
    return s._runtime


def _resolve_raw_plan(s: "Substrate") -> Any:
    import persistence.plan as _plan_mod

    return _plan_mod


def _resolve_raw_replay(s: "Substrate") -> Any:
    import persistence.replay as _replay_mod

    return _replay_mod


def _resolve_raw_txn(s: "Substrate") -> Any:
    import persistence.txn as _txn_mod

    return _txn_mod


def _resolve_raw_spec(s: "Substrate") -> Any:
    import persistence.spec as _spec_mod

    return _spec_mod


def _resolve_raw_repl(s: "Substrate") -> Any:
    import persistence.repl as _repl_mod

    return _repl_mod


_EscapeNamespace._RAW_RESOLVERS = {
    "fact": _resolve_raw_fact,
    "effect": _resolve_raw_effect,
    "plan": _resolve_raw_plan,
    "replay": _resolve_raw_replay,
    "txn": _resolve_raw_txn,
    "spec": _resolve_raw_spec,
    "repl": _resolve_raw_repl,
}


# ---------------------------------------------------------------------------
# Substrate
# ---------------------------------------------------------------------------
# The closed list of names ``dir(s)`` returns at the contract level.
# Per the brief's frozen-surface test: "dir(s) returns exactly the curated
# 7 subsurface names + lifecycle methods (no raw module bleed-through)."
# NOTE: Python's ``dir()`` sorts the returned list lexically; underscore
# names sort before lowercase letters (ASCII 0x5F < 0x61). The literal
# below preserves that lexical order so the contract-surface comparison
# in the frozen-surface test reads naturally.
_SUBSTRATE_PUBLIC_DIR: tuple[str, ...] = (
    # stability marker (class attribute)
    "_stability_version",
    # 7 curated subsurfaces
    "audit",
    "close",
    "effect",
    "escape",
    "fact",
    "open",
    "replay",
    "repl",
    "txn",
)


@stable("v0.8")
class Substrate:
    """Curated namespace + lifecycle facade for the v0.8 adapter contract.

    Open with :meth:`open`, close with :meth:`close`, or use as a
    context manager. The seven subsurface attributes (``fact`` /
    ``effect`` / ``txn`` / ``repl`` / ``audit`` / ``replay`` /
    ``escape``) are the contract surface for adapter authors.

    Lifecycle::

        s = Substrate.open("memory")
        # ... use s.fact.transact(...), s.txn.dosync(), etc.
        s.close()

        # Or as a context manager:
        with Substrate.open("sqlite:///path/to/db") as s:
            ...

    Per ADR-1 the curated namespaces (``fact`` / ``effect`` / ``txn``
    / ``repl`` / ``audit`` / ``replay``) are the v0.8 stable surface;
    ``escape`` is the explicit out-of-contract reach-through with
    audit-emission first-access telemetry.

    Per ADR-12 the SDK does NOT auto-start a REPL server; adapter
    authors call ``s.repl.serve(...)`` explicitly when they want one.
    """

    # Class attribute pinning the stability profile — read by the spec
    # generator (G7 / SDK5) and by adapter authors who introspect
    # ``Substrate._stability_version``. Per the design doc § 4:
    # "Class attribute :attr:`_stability_version = "v0.8"`".
    _stability_version: str = "v0.8"

    def __init__(
        self,
        *,
        _db: Any,
        _runtime: Any,
        _audit_entries: Optional[list] = None,
    ) -> None:
        # Private constructor; adapter authors use :meth:`open`.
        self._db = _db
        self._runtime = _runtime
        self._audit_entries: list[Any] = (
            list(_audit_entries) if _audit_entries is not None else []
        )
        self._closed = False
        # Per-substrate session id used in ADR-1 W3 NIT-5 escape-hatch
        # audit-entry payloads. v0.8 uses the system uuid generator at
        # construction time; the value never escapes the audit-entry
        # shape (which is itself ``@experimental`` per ADR-1 W3 NIT-5)
        # so it is not a contract surface for adapter authors. Routing
        # through ``:sys/random`` would require booting a runtime stack
        # at substrate-open time, which the v0.8 ``Substrate.open`` is
        # explicitly NOT in charge of (per ADR-1 + the design doc
        # § 4 ADR-1 W2 "no pre-installed audit handler" closure).
        self._session_id = uuid.uuid4().hex  # noqa: wall-clock
        # Subsurfaces are bound at construction time so each call site
        # gets the same namespace instance back (identity stability is
        # documented for adapter authors who cache references).
        self._fact = _FactNamespace(self)
        self._effect = _EffectNamespace(self)
        self._txn = _TxnNamespace(self)
        self._repl = _ReplNamespace(self)
        self._audit = _AuditNamespace(self)
        self._replay = _ReplayNamespace(self)
        self._escape = _EscapeNamespace(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, uri: str = "memory") -> "Substrate":
        """Open a substrate against the given store URI.

        Per ADR-9 the URI may be:

        - ``"memory"``                  — in-process in-memory store
        - ``"sqlite:///<absolute-path>"`` — file-backed SQLite store
        - ``"postgres://..."``          — PG1 stream (raises
          :class:`BackendNotInstalled` until PG1 lands)

        URI parsing + backend dispatch are delegated to
        :func:`persistence.sdk.uri.open_store`; SDK2 wires the resulting
        ``Store`` into a fresh :class:`persistence.fact.DB` and a bare
        :class:`persistence.effect.Runtime`. The runtime is intentionally
        empty in v0.8 — adapter authors who need an audit handler stack
        push one onto ``s.escape.effect.handlers`` (the design doc § 4
        docstring claims pre-installed audit but ADR-1 W2 escape-hatch
        telemetry pushed handler customization to the escape-hatch
        path; the curated ``s.effect`` surface stays small).

        Args:
            uri: store URI per ADR-9; default ``"memory"``.

        Returns:
            a fresh :class:`Substrate` ready for use.

        Raises:
            UnknownStoreScheme:   per :func:`open_store`.
            BackendNotInstalled:  per :func:`open_store`.
            ValueError:           per :func:`open_store`.
        """
        from persistence.effect import Runtime
        from persistence.fact import DB

        store = open_store(uri)
        db = DB(store)
        runtime = Runtime()
        return cls(_db=db, _runtime=runtime)

    def close(self) -> None:
        """Release substrate resources. Idempotent.

        Per the design doc § 4 lifecycle pin: ``close()`` must be safe to
        call multiple times. v0.8 closes the underlying store (when the
        store exposes a ``close``) and marks the substrate as closed so
        subsequent subsurface access raises :class:`RuntimeError`.

        REPL server lifetimes are NOT auto-shut down by ``close()`` per
        ADR-12 — adapter authors who started a server via
        ``s.repl.serve(...)`` are responsible for its teardown. (The
        design doc explicitly notes this.)
        """
        if self._closed:
            return
        self._closed = True
        # Best-effort store close; not every Store impl exposes one.
        store = getattr(self._db, "store", None)
        store_close = getattr(store, "close", None)
        if callable(store_close):
            try:
                store_close()
            except Exception:  # noqa: BLE001
                # Idempotent close must not raise on second teardown
                # paths; the store may already be closed by the
                # caller. Swallowing matches stdlib file-close
                # idempotency expectations for adapter ergonomics.
                pass

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "Substrate":
        if self._closed:
            raise RuntimeError(
                "Substrate.__enter__: this substrate is already closed"
            )
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Curated subsurface properties
    # ------------------------------------------------------------------
    @property
    def fact(self) -> _FactNamespace:
        """Curated ``s.fact.*`` namespace; see :class:`_FactNamespace`."""
        self._check_open("fact")
        return self._fact

    @property
    def effect(self) -> _EffectNamespace:
        """Curated ``s.effect.*`` namespace; see
        :class:`_EffectNamespace`."""
        self._check_open("effect")
        return self._effect

    @property
    def txn(self) -> _TxnNamespace:
        """Curated ``s.txn.*`` namespace; see :class:`_TxnNamespace`."""
        self._check_open("txn")
        return self._txn

    @property
    def repl(self) -> _ReplNamespace:
        """Curated ``s.repl.*`` namespace; see :class:`_ReplNamespace`."""
        self._check_open("repl")
        return self._repl

    @property
    def audit(self) -> _AuditNamespace:
        """Curated ``s.audit.*`` namespace; see :class:`_AuditNamespace`."""
        self._check_open("audit")
        return self._audit

    @property
    def replay(self) -> _ReplayNamespace:
        """Curated ``s.replay.*`` namespace; see
        :class:`_ReplayNamespace`."""
        self._check_open("replay")
        return self._replay

    @property
    def escape(self) -> _EscapeNamespace:
        """Out-of-contract escape-hatch namespace; see
        :class:`_EscapeNamespace`. Each first access per-session
        emits one ``:sdk/escape-hatch-access`` audit entry."""
        self._check_open("escape")
        return self._escape

    # ------------------------------------------------------------------
    # Frozen surface
    # ------------------------------------------------------------------
    def __dir__(self) -> list[str]:
        """Return the closed contract-surface name list.

        Per the design doc + the brief's frozen-surface test: ``dir(s)``
        is the load-bearing way for adapter authors (and the spec
        generator) to enumerate the contract surface. Returning a fixed
        tuple — instead of falling through to ``object.__dir__`` —
        prevents raw Module attributes from bleeding through.
        """
        return list(_SUBSTRATE_PUBLIC_DIR)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _check_open(self, attr: str) -> None:
        """Raise :class:`RuntimeError` if the substrate has been closed.

        Adapter authors who reach into a closed substrate get a clear
        error instead of a silent UnboundLocalError-on-_db.
        """
        if self._closed:
            raise RuntimeError(
                f"Substrate is closed; cannot access {attr!r} subsurface"
            )


__all__ = [
    "Substrate",
]
