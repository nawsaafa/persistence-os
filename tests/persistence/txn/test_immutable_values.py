"""Immutable-value validation — rev M of design doc."""
from dataclasses import dataclass
from decimal import Decimal

import pytest
from pyrsistent import pmap, pset, pvector

from persistence.txn import RefValueNotImmutable
from persistence.txn.ref import freeze, is_immutable_value


def test_pyrsistent_collections_accepted():
    assert is_immutable_value(pmap({"a": 1}))
    assert is_immutable_value(pvector([1, 2, 3]))
    assert is_immutable_value(pset([1, 2, 3]))


def test_frozen_builtins_accepted():
    assert is_immutable_value(frozenset([1, 2]))
    assert is_immutable_value((1, 2, 3))
    assert is_immutable_value("hello")
    assert is_immutable_value(42)
    assert is_immutable_value(True)
    assert is_immutable_value(3.14)
    assert is_immutable_value(Decimal("1.5"))
    assert is_immutable_value(b"bytes")
    assert is_immutable_value(None)


def test_frozen_dataclass_accepted():
    @dataclass(frozen=True)
    class Point:
        x: int
        y: int

    assert is_immutable_value(Point(1, 2))


def test_mutable_dict_rejected():
    assert not is_immutable_value({"a": 1})


def test_mutable_list_rejected():
    assert not is_immutable_value([1, 2, 3])


def test_mutable_set_rejected():
    assert not is_immutable_value({1, 2, 3})


def test_mutable_dataclass_rejected():
    @dataclass
    class Mutable:
        x: int

    assert not is_immutable_value(Mutable(1))


def test_freeze_dict_to_pmap():
    result = freeze({"a": 1, "b": 2})
    assert is_immutable_value(result)
    assert dict(result) == {"a": 1, "b": 2}


def test_freeze_list_to_pvector():
    result = freeze([1, 2, 3])
    assert is_immutable_value(result)
    assert list(result) == [1, 2, 3]


def test_freeze_passes_immutable_through():
    pm = pmap({"a": 1})
    assert freeze(pm) is pm
    assert freeze("hello") == "hello"
    assert freeze(42) == 42


def test_freeze_nested_dict_recurses():
    result = freeze({"outer": {"inner": [1, 2]}})
    assert is_immutable_value(result)
    inner = result["outer"]
    assert is_immutable_value(inner)
    inner_list = inner["inner"]
    assert is_immutable_value(inner_list)


def test_freeze_set_raises_RefValueNotImmutable():
    with pytest.raises(RefValueNotImmutable, match="set"):
        freeze({1, 2, 3})


def test_freeze_unknown_type_raises_RefValueNotImmutable():
    class Custom:
        pass

    with pytest.raises(RefValueNotImmutable, match="Custom"):
        freeze(Custom())
