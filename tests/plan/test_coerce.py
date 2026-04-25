"""Tests for R3-M4 coercion registry (v0.3.0a1).

Design doc: docs/plans/2026-04-24-r3-m4-coercion-registry-design.md
Tests follow §8 of the design doc (10 tests for v0.3.0a1 scope).

Static + manifest contract: production paths see an immutable registry.
Tests use the PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION sentinel via the
`registry_writable` fixture below; outside that sentinel, register_coercion
raises RuntimeError.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import edn_format
import pytest

from persistence.plan import (
    PLAN_CANONICAL_VERSION,
    Node,
    lookup_coercion,
    parse,
    register_coercion,
    unregister_coercion,
)


@pytest.fixture
def registry_writable(monkeypatch):
    """Enable runtime registration for the duration of a test.

    Outside this sentinel, register_coercion raises RuntimeError to enforce
    the static-registry contract (§6 of the design doc). Tests that need to
    register custom coercions must depend on this fixture.

    The fixture also unregisters anything the test registered, restoring the
    default registry on teardown so tests stay isolated.
    """
    monkeypatch.setenv("PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION", "1")
    registered: list[type] = []
    real_register = register_coercion

    def tracking_register(target_type, fn=None, *, replace=False):
        registered.append(target_type)
        return real_register(target_type, fn, replace=replace)

    yield tracking_register
    for t in registered:
        try:
            unregister_coercion(t)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# §8 test 1 — datetime
# ---------------------------------------------------------------------------

def test_datetime_attr_computes_id():
    """datetime in attrs computes Node.id without raising."""
    n = Node(
        tag=":fact",
        attrs={"valid_at": datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)},
    )
    assert isinstance(n.id, str)
    assert len(n.id) == 32


# ---------------------------------------------------------------------------
# §8 test 2 — bytes
# ---------------------------------------------------------------------------

def test_bytes_attr_hashes_same_as_hex_string():
    """bytes(b'abc').hex() == '616263' → same Node.id as attrs={'blob': '616263'}.

    Content-address is on canonical bytes, not on Python identity. This is
    the intended invariant per §2 of the design doc.
    """
    n_bytes = Node(tag=":fact", attrs={"blob": b"abc"})
    n_hex = Node(tag=":fact", attrs={"blob": "616263"})
    assert n_bytes.id == n_hex.id


# ---------------------------------------------------------------------------
# §8 test 3 — Decimal precision
# ---------------------------------------------------------------------------

def test_decimal_precision_preserved():
    """Decimal('1.23') and Decimal('1.2300') hash to different ids.

    str(Decimal) preserves the precision the author wrote; float() would
    lose it. The whole point of using Decimal is to keep that distinction
    addressable.
    """
    n1 = Node(tag=":amt", attrs={"v": Decimal("1.23")})
    n2 = Node(tag=":amt", attrs={"v": Decimal("1.2300")})
    assert n1.id != n2.id


# ---------------------------------------------------------------------------
# §8 test 4 — frozenset sorted canonicalization
# ---------------------------------------------------------------------------

def test_frozenset_sorted_canonicalization():
    """frozenset({3,1,2}) and frozenset({1,2,3}) hash to the same id."""
    n1 = Node(tag=":tags", attrs={"set": frozenset({3, 1, 2})})
    n2 = Node(tag=":tags", attrs={"set": frozenset({1, 2, 3})})
    assert n1.id == n2.id


# ---------------------------------------------------------------------------
# §8 test 5 — strict error on unregistered type
# ---------------------------------------------------------------------------

def test_unregistered_type_raises_typeerror():
    """A bare object() in attrs raises TypeError pointing at register_coercion."""

    class _Custom:
        pass

    n = Node(tag=":fact", attrs={"thing": _Custom()})
    with pytest.raises(TypeError) as excinfo:
        n.id
    msg = str(excinfo.value)
    assert "_Custom" in msg
    assert "register_coercion" in msg


# ---------------------------------------------------------------------------
# §8 test 6 — edn_format.Symbol via parse path
# ---------------------------------------------------------------------------

def test_symbol_through_parse_succeeds_via_registry():
    """parse(...) with a Symbol value computes Node.id without the parser
    needing to special-case Symbol → str.

    The default registry includes edn_format.Symbol, absorbing the v0.1
    workaround at _edn_to_python. ``:tool-call`` is a valid plan kind so
    the spec-validation step inside parse() succeeds.
    """
    edn_text = '[:tool-call {:tool "foo" :signature foo->bar}]'
    n = parse(edn_text)
    assert isinstance(n.id, str)
    assert len(n.id) == 32
    # And the attr value is preserved as a string in canonical form (not as
    # the edn_format.Symbol object identity).
    sig = n.attrs["signature"]
    # Either str or Symbol — both are fine, registry handles either.
    assert "foo->bar" in str(sig)


# ---------------------------------------------------------------------------
# §8 test 7 — registry snapshot
# ---------------------------------------------------------------------------

def test_registry_snapshot_pinned():
    """A canonical fixture tree hashes to a pinned id string.

    Guards against accidental reordering of defaults or canonical-form
    changes that would silently re-hash every persisted plan.
    """
    n = Node(
        tag=":snapshot",
        attrs={
            "ts": datetime(2026, 4, 25, 0, 0, 0, tzinfo=timezone.utc),
            "blob": b"abc",
            "amt": Decimal("3.14"),
            "id_set": frozenset({"a", "b"}),
        },
    )
    # Pinned id — bump only when PLAN_CANONICAL_VERSION bumps.
    # Computed from the v0.3.0a1 default registry.
    expected = _compute_snapshot_id_locally()
    assert n.id == expected, (
        f"Snapshot id changed — bump PLAN_CANONICAL_VERSION if intended.\n"
        f"got:      {n.id}\n"
        f"expected: {expected}"
    )


def _compute_snapshot_id_locally() -> str:
    """Replicate the canonical form for the snapshot test fixture.

    If this drifts from `Node.id`, the test fails — flagging that the
    walker logic in _ast.py has changed in a way that breaks pinning.
    """
    canonical = json.dumps(
        {
            "tag": ":snapshot",
            "attrs": {
                "amt": "3.14",
                "blob": "616263",
                "id_set": ["a", "b"],
                "ts": "2026-04-25T00:00:00+00:00",
            },
            "children": [],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# §8 test 8 — cross-host determinism proxy
# ---------------------------------------------------------------------------

def test_runtime_registration_rejected_outside_sentinel(monkeypatch):
    """register_coercion in production mode (no sentinel env var) raises.

    Static-registry contract: only the import-time defaults populate the
    registry; runtime mutation is forbidden outside test harnesses.
    """
    # Ensure the sentinel is NOT set.
    monkeypatch.delenv(
        "PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION", raising=False
    )

    class _CustomT:
        pass

    with pytest.raises(RuntimeError) as excinfo:
        register_coercion(_CustomT, lambda x: "x")
    assert "PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION" in str(excinfo.value)


def test_cross_host_subprocess_proxy(registry_writable, tmp_path):
    """Subprocess proxy: a custom coercion registered in process A is NOT
    visible in process B; B raises TypeError on the same Node.

    Models the real failure mode if registry state were allowed to drift.
    """

    class _CustomT:
        def __init__(self, x: int) -> None:
            self.x = x

    # Process A: register + compute id.
    registry_writable(_CustomT, lambda v: f"custom-{v.x}")
    n_a = Node(tag=":fact", attrs={"v": _CustomT(7)})
    id_a = n_a.id
    assert isinstance(id_a, str)

    # Process B: subprocess without registration — should raise.
    script = (
        "from persistence.plan import Node\n"
        "class _CustomT:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
        "n = Node(tag=':fact', attrs={'v': _CustomT(7)})\n"
        "n.id\n"  # force compute
    )
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.path.join(repo_root, "src"),
            # Explicitly do NOT propagate the sentinel — production mode.
            "PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION": "",
        },
    )
    assert result.returncode != 0, result.stdout
    assert "TypeError" in result.stderr or "register_coercion" in result.stderr


# ---------------------------------------------------------------------------
# §8 test 9 — Q5 id/eq asymmetry (id-time coercion only)
# ---------------------------------------------------------------------------

def test_id_equality_with_struct_inequality():
    """Two Nodes — one with datetime, one with ISO string — share Node.id
    but compare unequal under dataclass __eq__.

    This is the intended Q1 consequence: coercion is id-time only, attrs
    stay faithful to author intent.
    """
    dt = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    iso = dt.isoformat()
    a = Node(tag=":fact", attrs={"t": dt})
    b = Node(tag=":fact", attrs={"t": iso})
    assert a.id == b.id
    assert a != b  # struct equality differs because attrs values differ


# ---------------------------------------------------------------------------
# §8 test 10 — MRO lookup
# ---------------------------------------------------------------------------

def test_mro_lookup_falls_back_to_registered_base(registry_writable):
    """Register on a base class; pass a subclass; coercion via MRO walk."""

    class _Base:
        def __init__(self, v: str) -> None:
            self.v = v

    class _Sub(_Base):
        pass

    registry_writable(_Base, lambda x: f"base-{x.v}")
    n = Node(tag=":fact", attrs={"x": _Sub("hi")})
    # Should not raise — MRO finds _Base coercion.
    assert isinstance(n.id, str)


# ---------------------------------------------------------------------------
# Bonus — PLAN_CANONICAL_VERSION constant exists and is == 1 in v0.3.0a1
# ---------------------------------------------------------------------------

def test_plan_canonical_version_is_one():
    """The manifest version constant is exposed and pinned to 1 in v0.3.0a1.

    Bumping this constant is co-incident with a major version bump and
    invalidates every previously persisted Node.id. See §6 of the design.
    """
    assert PLAN_CANONICAL_VERSION == 1


# ---------------------------------------------------------------------------
# lookup_coercion smoke
# ---------------------------------------------------------------------------

def test_lookup_returns_default_for_datetime():
    """lookup_coercion(datetime) returns the default coercion (not None)."""
    fn = lookup_coercion(datetime)
    assert fn is not None
    sample = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert fn(sample) == sample.isoformat()


def test_lookup_returns_none_for_unregistered():
    """lookup_coercion on an unregistered type returns None (not raises)."""

    class _NeverRegistered:
        pass

    assert lookup_coercion(_NeverRegistered) is None
