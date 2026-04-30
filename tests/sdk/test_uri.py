"""Tests for ``persistence.sdk.uri`` (SDK1).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-9, ``open_store(uri)`` dispatches by scheme:

- ``"memory"``                  → :class:`InMemoryStore`
- ``"sqlite:///<abs-path>"``    → :class:`SQLiteStore`
- ``"postgres://..."``          → :class:`BackendNotInstalled` (PG1 stub)
- unknown scheme                → :class:`UnknownStoreScheme`

SDK1 only needs to assert dispatch behavior, query-param parsing, and the
PG1 stub. The full ``Substrate.open(store=...)`` wiring lands in SDK2.
"""
from __future__ import annotations

import os

import pytest

from persistence.fact import InMemoryStore, SQLiteStore
from persistence.sdk import (
    BackendNotInstalled,
    UnknownStoreScheme,
    open_store,
    register_backend,
)


# ---------------------------------------------------------------------------
# 1. Memory dispatch
# ---------------------------------------------------------------------------
class TestMemoryScheme:
    def test_returns_in_memory_store(self):
        store = open_store("memory")
        assert isinstance(store, InMemoryStore)

    def test_each_call_returns_a_fresh_store(self):
        a = open_store("memory")
        b = open_store("memory")
        assert a is not b

    def test_case_insensitive_scheme(self):
        # ADR-9 says scheme matching is case-insensitive on the prefix.
        # The bare-keyword "memory" form is also case-insensitive.
        store = open_store("MEMORY")
        assert isinstance(store, InMemoryStore)

    def test_memory_uri_form_is_rejected(self):
        # "memory" is a bare keyword per ADR-9, NOT a URI scheme. The
        # opener errors loudly so adapters that mistakenly use the URI
        # form don't silently get an InMemoryStore from a possibly-typoed
        # call site.
        with pytest.raises(ValueError, match="bare keyword"):
            open_store("memory:")


# ---------------------------------------------------------------------------
# 2. SQLite dispatch
# ---------------------------------------------------------------------------
class TestSqliteScheme:
    def test_returns_sqlite_store_with_path(self, tmp_path):
        db_path = str(tmp_path / "g6.db")
        store = open_store(f"sqlite:///{db_path}")
        assert isinstance(store, SQLiteStore)
        # Sanity: the store is functional — append/read round-trips.
        # (Just-asserting isinstance would not catch a constructor that
        # silently picked the wrong path.)
        assert os.path.exists(db_path)

    def test_in_memory_sqlite_via_path(self, tmp_path):
        # SQLite supports `:memory:` as a special path — the URI form
        # `sqlite:///` with an explicit empty path is rejected, but
        # `sqlite://?path=:memory:` exercises the query-param override.
        store = open_store("sqlite://?path=:memory:")
        assert isinstance(store, SQLiteStore)

    def test_query_param_path_overrides_url_path(self, tmp_path):
        # If both URL path AND ?path= query-param are present, the
        # query-param wins. Adapter authors who construct URIs
        # programmatically rely on this for ergonomics.
        explicit_path = str(tmp_path / "explicit.db")
        # Pass an arbitrary URL path that should be IGNORED.
        store = open_store(f"sqlite:///ignored.db?path={explicit_path}")
        assert isinstance(store, SQLiteStore)
        assert os.path.exists(explicit_path)
        # Sanity: the path that was *supposed* to be ignored should NOT
        # have been created in the cwd.
        assert not os.path.exists(
            os.path.join(os.getcwd(), "ignored.db")
        )

    def test_missing_path_raises(self):
        with pytest.raises(ValueError, match="missing path"):
            open_store("sqlite:")

    def test_query_string_with_unknown_kwargs_is_ignored_for_sqlite(
        self, tmp_path
    ):
        # SQLite v0.8 has no documented kwargs beyond ?path=; unknown
        # kwargs are silently ignored (additive-compatible — future
        # patches may add optional ones). This test pins the behavior.
        db_path = str(tmp_path / "k.db")
        store = open_store(f"sqlite:///{db_path}?journal=wal&unknown=foo")
        assert isinstance(store, SQLiteStore)


# ---------------------------------------------------------------------------
# 3. Postgres dispatch — placeholder until PG1.
# ---------------------------------------------------------------------------
class TestPostgresScheme:
    def test_postgres_raises_backend_not_installed(self):
        with pytest.raises(BackendNotInstalled, match="postgres"):
            open_store("postgres://user:pass@localhost:5432/mydb")

    def test_postgres_error_subclasses_import_error(self):
        # ADR-9 says BackendNotInstalled subclasses ImportError so
        # adapters that ``except ImportError`` catch it.
        with pytest.raises(ImportError):
            open_store("postgres://localhost/db")


# ---------------------------------------------------------------------------
# 4. Unknown scheme
# ---------------------------------------------------------------------------
class TestUnknownScheme:
    def test_unknown_scheme_raises(self):
        with pytest.raises(UnknownStoreScheme, match="unknown scheme"):
            open_store("redis://localhost:6379/0")

    def test_unknown_scheme_lists_registered_schemes(self):
        with pytest.raises(UnknownStoreScheme) as excinfo:
            open_store("redis://localhost")
        msg = str(excinfo.value)
        # All three v0.8 schemes should be advertised in the error.
        assert "memory" in msg
        assert "sqlite" in msg
        assert "postgres" in msg

    def test_unknown_scheme_subclasses_value_error(self):
        # ADR-9: UnknownStoreScheme subclasses ValueError so adapters
        # that catch broad parse errors still reach it.
        with pytest.raises(ValueError):
            open_store("redis://x")

    def test_empty_uri_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            open_store("")

    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            open_store(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. register_backend — extension point
# ---------------------------------------------------------------------------
class TestRegisterBackend:
    def test_register_then_open_dispatches(self):
        sentinel = InMemoryStore()
        sentinel_uri = "test-sdk1-fake-scheme"

        def opener(uri, kwargs):
            return sentinel

        try:
            register_backend(sentinel_uri, opener)
            got = open_store(f"{sentinel_uri}://anywhere")
            assert got is sentinel
        finally:
            # Clean up — leave the registry as we found it for other tests.
            from persistence.sdk import uri as uri_mod

            uri_mod._BACKENDS.pop(sentinel_uri, None)

    def test_register_existing_without_replace_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            register_backend("sqlite", lambda u, k: InMemoryStore())

    def test_register_existing_with_replace_succeeds(self, tmp_path):
        # Save existing opener and restore.
        from persistence.sdk import uri as uri_mod

        original = uri_mod._BACKENDS["sqlite"]
        try:
            register_backend(
                "sqlite", lambda u, k: InMemoryStore(), replace=True
            )
            got = open_store(f"sqlite:///{tmp_path}/x.db")
            assert isinstance(got, InMemoryStore)  # the swapped backend
        finally:
            uri_mod._BACKENDS["sqlite"] = original

    def test_register_empty_scheme_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            register_backend("", lambda u, k: InMemoryStore())


# ---------------------------------------------------------------------------
# 6. Substrate import + stability marker
# ---------------------------------------------------------------------------
class TestSubstrateImport:
    def test_import_works(self):
        # ``from persistence.sdk import Substrate`` worked since SDK1;
        # SDK2 fills in the body so calling ``Substrate.open()`` is the
        # canonical instantiation path. Direct ``Substrate()`` is no
        # longer the contract surface — adapter authors use ``open``.
        from persistence.sdk import Substrate

        s = Substrate.open("memory")
        try:
            assert isinstance(s, Substrate)
        finally:
            s.close()

    def test_substrate_carries_stable_marker(self):
        # SDK1 had the placeholder marked @experimental with the explicit
        # promotion-handoff string; SDK2 promotes to @stable("v0.8") in
        # lockstep with the body fill-in. This test was renamed from
        # ``test_substrate_carries_experimental_marker`` (SDK1) — the
        # SDK1 version asserted ``level == "experimental"`` and pinned
        # the placeholder reason string so SDK2's promotion produced a
        # clean visible diff. SDK2's contract is that the marker now
        # reads as ``stable / v0.8``.
        from persistence.sdk import Substrate

        assert Substrate.__sdk_stability__ == {
            "level": "stable",
            "version": "v0.8",
            "note": None,
        }
