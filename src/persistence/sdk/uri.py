"""URI scheme dispatch for ``Store`` backends.

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-9, ``Substrate.open(store=<uri>)`` accepts a single URI argument and
dispatches to a registered backend by scheme. SDK1 ships the low-level
dispatcher in this module; SDK2 wires it into ``Substrate.open``.

Three v0.8 backends:

- ``"memory"``                  — bare keyword (per ADR-9; no URI form),
                                  resolves to :class:`InMemoryStore`.
- ``sqlite:///<absolute-path>`` — RFC-3986 file URI form; resolves to
                                  :class:`SQLiteStore` opened against
                                  ``<absolute-path>``.
- ``postgres://...``            — Phase 1 stream #137 (PG1).
                                  SDK1 stubs the arm to raise
                                  :class:`BackendNotInstalled` until PG1
                                  lands the real ``PostgresStore``.

Query-string parameters are parsed and forwarded as keyword arguments to
the chosen backend constructor. Unknown schemes raise
:class:`UnknownStoreScheme`. The dispatcher is registry-driven so future
backends slot in via :func:`register_backend` (kept private to the SDK
namespace until ADR-9's plugin point lands; v0.8 only registers the
in-tree backends).

This module imports nothing from ``persistence.sdk`` itself, which keeps
the dependency graph clean: ``persistence.sdk.__init__`` re-exports
``open_store`` for the public surface but ``open_store`` does NOT import
the facade.
"""
from __future__ import annotations

from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from persistence.fact import InMemoryStore, SQLiteStore, Store


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class UnknownStoreScheme(ValueError):
    """Raised when an unknown URI scheme is passed to :func:`open_store`.

    The error message lists the registered schemes for adopter ergonomics.
    Subclasses :class:`ValueError` for compatibility with adapters that
    catch the broad parse-error class.
    """


class BackendNotInstalled(ImportError):
    """Raised when a known scheme references a backend whose dependencies
    are not installed.

    Per ADR-9 the canonical example is ``postgres://...`` without the
    ``[postgres]`` extra (which provides ``psycopg``). Subclasses
    :class:`ImportError` so adopters can ``except ImportError`` if they
    only care about "the package didn't ship that backend."
    """


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------
# Each entry is ``(opener_callable, doc)`` where ``opener_callable`` takes
# ``(uri_str, parsed_kwargs) -> Store``. The registry is module-level state
# but is populated exactly once at import time below; callers who need to
# extend it use :func:`register_backend`.
_BackendOpener = Callable[[str, dict[str, str]], Store]
_BACKENDS: dict[str, _BackendOpener] = {}


def register_backend(
    scheme: str, opener: _BackendOpener, *, replace: bool = False
) -> None:
    """Register a backend opener for a URI scheme.

    Kept private-API for v0.8 (the SDK only registers the three in-tree
    backends). v0.9+ may make this a documented plugin point.

    Args:
        scheme: lowercase URI scheme, e.g. ``"sqlite"``, ``"postgres"``,
            or the bare keyword ``"memory"``.
        opener: callable ``(uri_str, parsed_kwargs) -> Store``. The opener
            is responsible for any URI-shape validation beyond scheme
            dispatch.
        replace: when ``False`` (default), raises ``ValueError`` if the
            scheme is already registered. ``True`` is used by tests that
            need to swap a backend for a fixture.

    Raises:
        ValueError: if ``scheme`` is empty or already registered with
            ``replace=False``.
    """
    if not isinstance(scheme, str) or not scheme:
        raise ValueError(
            f"register_backend: scheme must be a non-empty string, "
            f"got {scheme!r}"
        )
    if scheme in _BACKENDS and not replace:
        raise ValueError(
            f"register_backend: scheme {scheme!r} is already registered; "
            f"pass replace=True to override."
        )
    _BACKENDS[scheme] = opener


def _registered_schemes() -> list[str]:
    """Return the registered scheme list, sorted for deterministic error
    messages."""
    return sorted(_BACKENDS)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------
def open_store(uri: str) -> Store:
    """Open a ``Store`` backend by URI.

    Args:
        uri: one of:

            - ``"memory"``               — bare keyword; in-process
                                           in-memory store.
            - ``"sqlite:///<abs-path>"`` — file-backed SQLite store.
            - ``"postgres://..."``       — Postgres backend (PG1).

            Query-string parameters (``?k=v&...``) are parsed and
            forwarded as kwargs to the backend constructor. Per ADR-9 the
            scheme matching is case-insensitive on the scheme prefix only;
            paths and query values are passed through verbatim.

    Returns:
        a fresh :class:`Store` instance.

    Raises:
        UnknownStoreScheme:   if ``uri`` does not match any registered
                              scheme.
        BackendNotInstalled:  if the scheme is known but the backend's
                              optional dependencies are not installed
                              (currently only ``postgres://`` until PG1).
        ValueError:           if the URI is malformed (e.g. empty, or a
                              scheme requires components it doesn't have).
    """
    if not isinstance(uri, str):
        raise ValueError(f"open_store: uri must be a string, got {uri!r}")
    if not uri:
        raise ValueError("open_store: uri is empty")

    # Bare keyword path: "memory" — no URI form, no query params.
    # The bare keyword is also accepted via the scheme registry below
    # (for ergonomic parity with future bare keywords) but checked first
    # so a stray URL parser doesn't decide "memory" is a relative path.
    scheme_lower = uri.split(":", 1)[0].lower() if ":" in uri else uri.lower()
    if scheme_lower not in _BACKENDS:
        raise UnknownStoreScheme(
            f"open_store: unknown scheme {scheme_lower!r} in uri "
            f"{uri!r}; registered schemes: {_registered_schemes()}"
        )

    opener = _BACKENDS[scheme_lower]

    # Query-string parsing for URI-form schemes only (scheme:...).
    # ``urlsplit`` returns empty .query / .scheme for the bare keyword
    # form, which is the right shape for the bare-keyword opener.
    if ":" in uri:
        split = urlsplit(uri)
        # Coerce ?k=v&k=w into {"k": "w"} (last-write-wins per parse_qs
        # default). Backends that need list-shaped params will reach into
        # the raw URI; v0.8's three backends are scalar-only.
        parsed = parse_qs(split.query, keep_blank_values=True)
        kwargs: dict[str, str] = {k: v[-1] for k, v in parsed.items()}
    else:
        kwargs = {}

    return opener(uri, kwargs)


# ---------------------------------------------------------------------------
# Built-in backend openers
# ---------------------------------------------------------------------------
def _open_memory(uri: str, kwargs: dict[str, str]) -> Store:
    """Bare ``"memory"`` keyword opener.

    The in-memory store has no constructor parameters in v0.8; any
    query-string parameters are ignored. (We don't error on unknown
    kwargs because future v0.8.x patches may add optional ones, and
    ignoring is the additive-compatible choice.)
    """
    if uri.lower() != "memory":
        raise ValueError(
            f"_open_memory: 'memory' is a bare keyword, not a URI; "
            f"got {uri!r}"
        )
    return InMemoryStore()


def _open_sqlite(uri: str, kwargs: dict[str, str]) -> Store:
    """``sqlite:///<path>`` opener.

    The path component (after ``sqlite://``) is forwarded to
    :class:`SQLiteStore` as its ``path`` argument. Per RFC-3986 the
    ``sqlite:///`` form (three slashes) gives an absolute path; the
    ``sqlite:`` form with an explicit query string only is rejected with
    a ValueError because the path is the whole point.

    A ``?path=`` query parameter is honored as an override for adopters
    who construct the URI programmatically without remembering the
    triple-slash convention; explicit kwarg wins over the URL path.
    """
    split = urlsplit(uri)
    # split.path begins with a leading slash on a triple-slash form
    # ("sqlite:///foo" → split.path == "/foo"); SQLite treats "/foo" as
    # absolute on POSIX which matches the ADR-9 contract. We keep the
    # leading slash exactly as urlsplit produced it.
    path = split.path or ""
    # Allow ?path= override for adopter ergonomics (and to support the
    # SDK1 unit test that exercises query-param forwarding).
    if "path" in kwargs:
        path = kwargs.pop("path")
    if not path:
        raise ValueError(
            f"_open_sqlite: missing path in {uri!r} (use "
            f"'sqlite:///<absolute-path>' or 'sqlite://?path=<path>')"
        )
    return SQLiteStore(path=path)


def _open_postgres(uri: str, kwargs: dict[str, str]) -> Store:
    """``postgres://...`` opener — wires through to PG1's PostgresStore.

    Per Adapter SDK ADR-9 + Phase 1 stream #137 (PG1):

    1. Lazy-import :class:`persistence.store.postgres.PostgresStore`. If
       the ``[postgres]`` extra is not installed (``psycopg`` /
       ``psycopg_pool`` unavailable), raise
       :class:`BackendNotInstalled` with a clean install hint — the
       contract guarantees adopters that ``except ImportError`` catch
       this path uniformly across schemes.
    2. Pass the URI through to PostgresStore as the libpq DSN. psycopg
       accepts both the ``postgres://`` and ``postgresql://`` schemes
       directly in the DSN body so no URI rewriting is needed for the
       DSN body itself; the ``open_store`` registry only knows
       ``postgres`` though, so callers who want the ``postgresql://``
       body must construct the URI accordingly.
    3. Honour optional pool-tuning query params: ``?pool_min=N``,
       ``?pool_max=N``, ``?pool_timeout=S``. These are popped from the
       kwargs dict AND stripped from the URI's query string BEFORE the
       DSN reaches libpq so libpq does not reject the unknown
       ``pool_*`` keys. Any other query params (sslmode,
       application_name, etc.) stay in the DSN and reach psycopg.

    Args:
        uri: full ``postgres://...`` DSN.
        kwargs: parsed query-string kwargs (last-write-wins per
            :func:`open_store`).

    Returns:
        a fresh :class:`PostgresStore`.

    Raises:
        BackendNotInstalled: if psycopg or psycopg_pool are not
            installed. Message includes the pip install hint.
        ValueError: if a pool-tuning kwarg is malformed (non-numeric
            ``pool_min`` etc.).
    """
    # Pop the SDK-level pool-tuning kwargs BEFORE we hand the DSN to
    # libpq — libpq does not understand ``pool_*`` keys and would
    # reject the connection with "invalid URI query parameter".
    pool_kwargs: dict[str, Any] = {}
    for k_int in ("pool_min", "pool_max"):
        if k_int in kwargs:
            raw = kwargs.pop(k_int)
            try:
                pool_kwargs[k_int] = int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"_open_postgres: {k_int}={raw!r} is not an integer"
                ) from exc
    if "pool_timeout" in kwargs:
        raw = kwargs.pop("pool_timeout")
        try:
            pool_kwargs["pool_timeout"] = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"_open_postgres: pool_timeout={raw!r} is not a number"
            ) from exc

    # Strip the popped kwargs from the URI's query string before
    # forwarding to libpq. Rebuild with the residual query intact so
    # e.g. ``sslmode=require`` still reaches psycopg.
    if pool_kwargs and ":" in uri:
        split = urlsplit(uri)
        if split.query:
            consumed = {"pool_min", "pool_max", "pool_timeout"}
            kept: list[str] = []
            for pair in split.query.split("&"):
                if not pair:
                    continue
                k = pair.split("=", 1)[0]
                if k not in consumed:
                    kept.append(pair)
            new_query = "&".join(kept)
            # Reconstruct using SplitResult._replace — keeps scheme +
            # netloc + path + fragment exactly.
            uri = split._replace(query=new_query).geturl()

    # Lazy import — module-level import would force psycopg into the
    # substrate's required dependency closure, which is exactly what
    # the optional-extras pattern exists to avoid.
    try:
        from persistence.store.postgres import PostgresStore
    except ImportError as exc:
        # Two reasons this can fire: (a) psycopg / psycopg_pool not
        # installed (the [postgres] extra was not requested at install
        # time); (b) the persistence.store package itself failed to
        # import for some other reason (highly unlikely). The contract
        # says BackendNotInstalled subclasses ImportError so adopters
        # that ``except ImportError`` catch (a) cleanly.
        raise BackendNotInstalled(
            f"postgres:// backend requires the [postgres] extra: "
            f'pip install "persistence[postgres]"  '
            f"(missing: {exc.name or exc})"
        ) from exc

    # Hand the cleaned URI through to PostgresStore — psycopg's DSN
    # parser handles both ``postgres://`` and ``postgresql://`` body
    # forms plus any query params we didn't pop (sslmode, etc.).
    return PostgresStore(dsn=uri, **pool_kwargs)


# Register the three v0.8 backends at import time. The order matches
# ADR-9's documentation order (memory → sqlite → postgres) which the spec
# generator (G7 / SDK5) reads when emitting the contract surface.
register_backend("memory", _open_memory)
register_backend("sqlite", _open_sqlite)
register_backend("postgres", _open_postgres)


__all__ = [
    "BackendNotInstalled",
    "UnknownStoreScheme",
    "open_store",
    "register_backend",
]
