"""Tests for ``persistence.sdk._facade.Substrate`` (SDK2).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-1 + ADR-12 + § 4, SDK2 fills in the lifecycle methods, the seven
curated subsurface namespaces, and the escape-hatch first-access
telemetry path. SDK1's empty-placeholder frozen test is updated to
assert the promoted ``@stable("v0.8")`` marker.

Test groups:

1. Lifecycle — ``Substrate.open / close / __enter__ / __exit__``.
2. Stability marker — ``__sdk_stability__`` is now ``stable / v0.8``.
3. Curated subsurface dispatch — each of the six namespaces (``fact``
   / ``effect`` / ``txn`` / ``repl`` / ``audit`` / ``replay``) has at
   least one happy-path test calling a method and confirming it
   reaches the underlying impl.
4. Escape-hatch namespace — ``s.escape.<module>`` returns the raw
   underlying surface; first access emits exactly one
   ``:sdk/escape-hatch-access`` audit entry; subsequent accesses are
   silent; the entry payload carries the ADR-1 W3 NIT-5 shape.
5. Frozen surface — ``dir(s)`` is the closed contract list; no
   raw module names bleed through.
6. Audit-helper experimental marker — the helper itself carries
   ``__sdk_stability__["level"] == "experimental"`` per ADR-1 W3 NIT-5.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.fact import DB, InMemoryStore, SQLiteStore
from persistence.sdk import Substrate
from persistence.sdk._audit import (
    ESCAPE_HATCH_MODULES,
    build_escape_hatch_payload,
)
from persistence.sdk._facade import (
    _SUBSTRATE_PUBLIC_DIR,
    _AuditNamespace,
    _EffectNamespace,
    _EscapeNamespace,
    _FactNamespace,
    _ReplayNamespace,
    _ReplNamespace,
    _TxnNamespace,
)


# ===========================================================================
# 1. Lifecycle — open / close / context manager
# ===========================================================================
class TestLifecycle:
    def test_open_memory_returns_working_substrate(self):
        s = Substrate.open("memory")
        try:
            assert isinstance(s, Substrate)
            # The substrate's underlying DB is functional — a transact
            # round-trip proves the URI dispatch wired the InMemoryStore
            # in correctly.
            assert isinstance(s.fact._substrate._db, DB)
            assert isinstance(s.fact._substrate._db.store, InMemoryStore)
        finally:
            s.close()

    def test_open_default_uri_is_memory(self):
        # Per ADR-9 ergonomics: bare ``Substrate.open()`` should pick the
        # memory backend so adapter examples ("open + remember") work
        # without a URI argument.
        s = Substrate.open()
        try:
            assert isinstance(s.fact._substrate._db.store, InMemoryStore)
        finally:
            s.close()

    def test_open_sqlite_uri(self, tmp_path):
        db_path = str(tmp_path / "facade.db")
        s = Substrate.open(f"sqlite:///{db_path}")
        try:
            assert isinstance(s.fact._substrate._db.store, SQLiteStore)
        finally:
            s.close()

    def test_close_marks_substrate_closed(self):
        s = Substrate.open("memory")
        s.close()
        # Subsurface access on a closed substrate raises rather than
        # silently dereferencing a stale store.
        with pytest.raises(RuntimeError, match="closed"):
            _ = s.fact

    def test_close_is_idempotent(self):
        # ADR / design § 4 lifecycle pin: idempotent close. The second
        # call must not raise.
        s = Substrate.open("memory")
        s.close()
        s.close()  # no error
        s.close()  # still no error

    def test_context_manager_opens_and_closes(self):
        with Substrate.open("memory") as s:
            assert isinstance(s, Substrate)
            # Subsurface access works inside the block.
            _ = s.fact
        # After the block, the substrate is closed.
        with pytest.raises(RuntimeError, match="closed"):
            _ = s.fact

    def test_context_manager_returns_self(self):
        s = Substrate.open("memory")
        try:
            with s as inner:
                assert inner is s
        finally:
            s.close()

    def test_context_manager_rejects_already_closed(self):
        s = Substrate.open("memory")
        s.close()
        with pytest.raises(RuntimeError, match="already closed"):
            with s:
                pass


# ===========================================================================
# 2. Stability marker promoted to @stable("v0.8")
# ===========================================================================
class TestStableMarker:
    def test_substrate_carries_stable_marker(self):
        # SDK1 placeholder was @experimental; SDK2 promotes to
        # @stable("v0.8"). The frozen-surface test from SDK1 was pinned
        # to detect this swap; here is its replacement.
        assert Substrate.__sdk_stability__ == {
            "level": "stable",
            "version": "v0.8",
            "note": None,
        }

    def test_stability_version_class_attribute(self):
        # Per design doc § 4 the class carries an explicit
        # ``_stability_version = "v0.8"`` class attribute (separate from
        # the decorator metadata, used by adapter introspection).
        assert Substrate._stability_version == "v0.8"


# ===========================================================================
# 3. Curated subsurfaces — happy-path dispatch
# ===========================================================================
class TestFactNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.fact, _FactNamespace)

    def test_transact_dispatches_to_db(self):
        # A transact on s.fact appends a fact dict to the underlying log;
        # the round-trip via ``s.escape.fact.log()`` confirms dispatch.
        # Per ``DB.transact`` (fact/db.py:137) the input is a list of
        # ``{"e", "a", "v", "valid_from"}`` dicts; ``valid_from`` must
        # be a tz-aware datetime per ``_coerce_dt`` in fact/db.py:601.
        ts = datetime(2026, 4, 30, tzinfo=timezone.utc)
        with Substrate.open("memory") as s:
            s.fact.transact([
                {"e": "alice", "a": "name", "v": "Alice", "valid_from": ts}
            ])
            log = list(s.escape.fact.log())
            user = [x for x in log if x.a == "name"]
            assert len(user) == 1
            assert user[0].e == "alice"
            assert user[0].v == "Alice"

    def test_as_of_returns_view(self):
        ts = datetime(2026, 4, 30, tzinfo=timezone.utc)
        later = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with Substrate.open("memory") as s:
            s.fact.transact([
                {"e": "alice", "a": "name", "v": "Alice", "valid_from": ts}
            ])
            view = s.fact.as_of(later)
            # The DBView API mirrors the underlying impl; we just
            # confirm dispatch produced a non-None result.
            assert view is not None


class TestEffectNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.effect, _EffectNamespace)

    def test_perform_dispatches_to_runtime(self):
        # Inject a tiny handler so the runtime stack covers ``:test/op``;
        # we route via ``s.escape.effect.handlers`` because handler
        # customization is a reach-through scenario per ADR-1.
        from persistence.effect import Handler

        with Substrate.open("memory") as s:
            handler = Handler(
                name="test_handler",
                wraps={":test/op"},
                clauses={
                    ":test/op": lambda args, k, ctx: {"echo": args.get("v")},
                },
            )
            s.escape.effect.handlers.append(handler)
            result = s.effect.perform(":test/op", {"v": 42})
            assert result == {"echo": 42}

    def test_is_well_formed_dispatches(self):
        # An empty handler stack is well-formed against an empty catalog
        # and not well-formed against a non-empty one.
        with Substrate.open("memory") as s:
            assert s.effect.is_well_formed([]) is True
            assert s.effect.is_well_formed([":bogus/op"]) is False


class TestTxnNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.txn, _TxnNamespace)

    def test_dosync_context_manager_form(self):
        with Substrate.open("memory") as s:
            # Allocate a Ref, mutate via dosync, observe the new value.
            # Per ``tests/persistence/txn/test_dosync_basics.py`` the
            # canonical access path is ``tx.deref(r)`` / ``tx.assoc(r, v)``
            # — Refs are dereferenced through the transaction object,
            # NOT via ``ref.deref()`` (no such method on Ref).
            r = s.txn.new_ref(0)
            with s.txn.dosync() as tx:
                tx.assoc(r, 7)
            # After commit, observe the value via the post-commit DBView.
            view = s.fact.as_of(s.escape.fact._clock())
            ent = view.entity(r.eid)
            assert ent is not None
            assert ent.get("value") == 7


class TestReplNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.repl, _ReplNamespace)

    def test_serve_returns_wsserver_instance(self):
        # Per ADR-12 the SDK does NOT auto-start the server. ``s.repl.serve``
        # constructs a ``WSServer`` but does NOT start the run-loop;
        # adapter code drives ``server.serve(...)`` itself when needed.
        from persistence.repl import WSServer

        with Substrate.open("memory") as s:
            server = s.repl.serve()
            assert isinstance(server, WSServer)


class TestAuditNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.audit, _AuditNamespace)

    def test_verify_chain_on_empty_audit_log_is_true(self):
        with Substrate.open("memory") as s:
            assert s.audit.verify_chain() is True

    def test_entries_returns_immutable_snapshot(self):
        with Substrate.open("memory") as s:
            entries = s.audit.entries()
            assert isinstance(entries, tuple)
            # Snapshot — mutating the snapshot does not affect future
            # reads (it can't, because tuples are immutable). Sanity.
            with pytest.raises(AttributeError):
                entries.append("x")  # type: ignore[attr-defined]


class TestReplayNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.replay, _ReplayNamespace)

    def test_replay_module_callables_are_thin_passthroughs(self):
        # The three replay primitives are imported lazily inside each
        # method; we assert the bound-method exists and is callable.
        with Substrate.open("memory") as s:
            assert callable(s.replay.record)
            assert callable(s.replay.replay)
            assert callable(s.replay.compare)


# ===========================================================================
# 4. Escape-hatch namespace + audit telemetry
# ===========================================================================
class TestEscapeNamespace:
    def test_namespace_type(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.escape, _EscapeNamespace)

    def test_escape_fact_returns_raw_db(self):
        with Substrate.open("memory") as s:
            assert isinstance(s.escape.fact, DB)

    def test_escape_effect_returns_raw_runtime(self):
        from persistence.effect import Runtime

        with Substrate.open("memory") as s:
            assert isinstance(s.escape.effect, Runtime)

    def test_escape_plan_returns_raw_module(self):
        import persistence.plan as plan_mod

        with Substrate.open("memory") as s:
            assert s.escape.plan is plan_mod

    def test_escape_replay_returns_raw_module(self):
        import persistence.replay as replay_mod

        with Substrate.open("memory") as s:
            assert s.escape.replay is replay_mod

    def test_escape_txn_returns_raw_module(self):
        import persistence.txn as txn_mod

        with Substrate.open("memory") as s:
            assert s.escape.txn is txn_mod

    def test_escape_spec_returns_raw_module(self):
        import persistence.spec as spec_mod

        with Substrate.open("memory") as s:
            assert s.escape.spec is spec_mod

    def test_escape_repl_returns_raw_module(self):
        import persistence.repl as repl_mod

        with Substrate.open("memory") as s:
            assert s.escape.repl is repl_mod

    def test_unknown_escape_attribute_raises(self):
        with Substrate.open("memory") as s:
            with pytest.raises(AttributeError, match="no attribute"):
                _ = s.escape.bogus  # type: ignore[attr-defined]

    def test_first_access_emits_audit_entry(self):
        # Per ADR-1 W2 SHOULD-FIX 3 + W3 NIT-5: first access emits one
        # entry; payload carries module + caller_filename + session_id.
        with Substrate.open("memory") as s:
            assert s.audit.entries() == ()
            _ = s.escape.fact
            entries = s.audit.entries()
            assert len(entries) == 1
            entry = entries[0]
            assert entry["op"] == ":sdk/escape-hatch-access"
            args = entry["args"]
            assert args["module"] == "fact"
            assert args["session_id"] == s._session_id
            assert isinstance(args["caller_filename"], str)
            # ``caller_filename`` is the BASENAME (no full path) so the
            # audit log doesn't leak the user's home directory.
            assert "/" not in args["caller_filename"]
            assert "\\" not in args["caller_filename"]

    def test_subsequent_accesses_to_same_attr_are_silent(self):
        # The dedupe is per-session, per-attribute; repeat access to
        # ``s.escape.fact`` does NOT emit a second entry.
        with Substrate.open("memory") as s:
            _ = s.escape.fact
            _ = s.escape.fact
            _ = s.escape.fact
            assert len(s.audit.entries()) == 1

    def test_distinct_attrs_emit_distinct_entries(self):
        # Each escape-hatch attribute has its own dedupe slot — touching
        # ``fact`` then ``effect`` produces two entries.
        with Substrate.open("memory") as s:
            _ = s.escape.fact
            _ = s.escape.effect
            entries = s.audit.entries()
            assert len(entries) == 2
            assert entries[0]["args"]["module"] == "fact"
            assert entries[1]["args"]["module"] == "effect"

    def test_audit_helper_experimental_marker(self):
        # Per ADR-1 W3 NIT-5: the helper's stability marker says
        # ``experimental`` so the spec generator (G7 / SDK5) excludes
        # the audit-entry shape from the v0.8 contract surface. The
        # design doc + brief use ``"status"`` / ``"level"`` somewhat
        # interchangeably; the actual key written by the SDK1
        # ``@experimental`` decorator is ``"level"`` — pin that here so
        # the brief's "status" wording does not accidentally become a
        # second contract surface.
        assert build_escape_hatch_payload.__sdk_stability__["level"] == (
            "experimental"
        )

    def test_escape_hatch_modules_set_is_complete(self):
        # All seven names from ADR-1 are present in the helper's
        # closed set so a typo in caller code is rejected loudly.
        assert ESCAPE_HATCH_MODULES == frozenset(
            {"fact", "effect", "plan", "replay", "txn", "spec", "repl"}
        )


# ===========================================================================
# 5. Frozen surface — dir(s) is the closed contract list
# ===========================================================================
class TestFrozenSurface:
    def test_dir_is_closed_to_contract_surface(self):
        # Python's ``dir(obj)`` sorts the result of ``__dir__`` lexically
        # before returning it, so we compare against the sorted form of
        # the contract-surface list; the underlying tuple in the source
        # is in lexical order regardless.
        with Substrate.open("memory") as s:
            names = dir(s)
            assert names == sorted(_SUBSTRATE_PUBLIC_DIR)

    def test_dir_contains_all_seven_subsurfaces(self):
        with Substrate.open("memory") as s:
            names = set(dir(s))
            for sub in (
                "fact", "effect", "txn", "repl", "audit", "replay", "escape",
            ):
                assert sub in names

    def test_dir_contains_lifecycle_methods(self):
        with Substrate.open("memory") as s:
            names = set(dir(s))
            assert "open" in names
            assert "close" in names

    def test_dir_does_not_leak_raw_module_names(self):
        # Raw module attribute names that exist on the underlying
        # instances (``DB.transact_batch``, ``DB.history``, etc.) must
        # NOT appear at the top-level ``dir(s)`` — they live inside the
        # curated namespaces (``s.fact.transact_batch`` does work; it's
        # ``s.transact_batch`` that must NOT).
        with Substrate.open("memory") as s:
            names = set(dir(s))
            for raw in ("transact", "transact_batch", "history", "as_of",
                        "perform", "dosync", "verify_chain"):
                assert raw not in names

    def test_dir_includes_stability_version(self):
        # The class attribute ``_stability_version`` is the
        # introspection hook for the spec generator; it's part of the
        # contract surface.
        with Substrate.open("memory") as s:
            assert "_stability_version" in dir(s)


# ===========================================================================
# 6. Build escape-hatch payload — argument validation
# ===========================================================================
class TestBuildEscapeHatchPayload:
    def test_rejects_unknown_module(self):
        with pytest.raises(ValueError, match="unknown module"):
            build_escape_hatch_payload(
                "bogus", session_id="abc"
            )

    def test_rejects_empty_session_id(self):
        with pytest.raises(ValueError, match="session_id"):
            build_escape_hatch_payload("fact", session_id="")

    def test_rejects_non_string_session_id(self):
        with pytest.raises(ValueError, match="session_id"):
            build_escape_hatch_payload(
                "fact", session_id=None  # type: ignore[arg-type]
            )

    def test_emits_canonical_shape(self):
        # caller_depth semantics (per ``_resolve_caller_filename`` in
        # ``_audit.py``): depth 0 = the resolver itself, depth 1 =
        # ``build_escape_hatch_payload``, depth 2 = the direct caller
        # (i.e. this test). The default value of 2 is correct for
        # direct calls; ``__getattr__`` on ``_EscapeNamespace`` uses 3
        # because there's an extra frame between the helper and the
        # adapter source.
        payload = build_escape_hatch_payload(
            "effect", session_id="sess123", caller_depth=2
        )
        assert set(payload) == {"module", "caller_filename", "session_id"}
        assert payload["module"] == "effect"
        assert payload["session_id"] == "sess123"
        # caller_filename is the basename of THIS test file at depth 2.
        assert payload["caller_filename"].endswith("test_facade.py")
