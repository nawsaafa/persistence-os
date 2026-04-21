"""Effect catalog tests — 15-op registry, args/returns schemas."""
import pytest

from persistence.effect.catalog import (
    CATALOG,
    OP_NAMES,
    OpSpec,
    validate_args,
)


def test_catalog_has_exactly_15_ops():
    """Spec §1 catalog = 15 operations, no more, no fewer."""
    assert len(CATALOG) == 15
    assert len(OP_NAMES) == 15


def test_catalog_covers_all_spec_ops():
    expected = {
        ":llm/call",
        ":tool/call",
        ":mem/read",
        ":mem/write",
        ":decide",
        ":ask-user",
        ":emit-artifact",
        ":sleep",
        ":random",
        ":env/read",
        ":net/fetch",
        ":secret/use",
        ":cost/charge",
        ":clock/now",
        ":audit/emit",
    }
    assert OP_NAMES == expected


def test_every_op_has_args_and_returns_schema():
    for name, spec in CATALOG.items():
        assert isinstance(spec, OpSpec), f"{name} is not an OpSpec"
        assert isinstance(spec.args, dict), f"{name}.args is not a dict"
        # returns can be dict or None (sleep, audit/emit)
        assert spec.returns is None or isinstance(spec.returns, dict)


def test_validate_args_accepts_well_typed_call():
    """llm/call expects model, messages — well-typed payload passes."""
    validate_args(":llm/call", {"model": "claude", "messages": [{"role": "user"}]})


def test_validate_args_rejects_missing_required_field():
    with pytest.raises(ValueError, match="missing"):
        validate_args(":llm/call", {"messages": []})  # no :model


def test_validate_args_rejects_wrong_type():
    with pytest.raises(TypeError):
        validate_args(":llm/call", {"model": 42, "messages": []})


def test_validate_args_rejects_unknown_op():
    with pytest.raises(KeyError):
        validate_args("does/not/exist", {})


def test_validate_args_tolerates_extra_fields():
    """Extra args are allowed — handlers may add auxiliary metadata."""
    validate_args(":llm/call", {"model": "m", "messages": [], "extra": 1})


def test_clock_now_takes_empty_args():
    validate_args(":clock/now", {})


def test_sleep_requires_int_ms():
    validate_args(":sleep", {"ms": 100})
    with pytest.raises(TypeError):
        validate_args(":sleep", {"ms": "100"})
