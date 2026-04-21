"""ARIS R3 F7 -- persistence.effect must expose its public surface.

``__all__ = []`` hides every public name; Phase-2 consumers must reach
into private-ish submodules, and IDE auto-import never surfaces anything.
This test locks in the re-export contract.
"""
from __future__ import annotations


def test_effect_all_is_non_empty():
    import persistence.effect as pe
    assert pe.__all__, "persistence.effect.__all__ must not be empty"


def test_core_runtime_importable_from_package():
    # from persistence.effect import perform, Runtime, Handler, with_runtime, mask, named
    from persistence.effect import (
        Effect,
        Handler,
        Runtime,
        Unhandled,
        mask,
        named,
        perform,
        with_runtime,
    )
    # sanity: constructed from re-exported classes
    rt = Runtime(handlers=[])
    assert isinstance(rt, Runtime)


def test_audit_api_importable_from_package():
    from persistence.effect import (
        AuditEntry,
        audit_entry_to_datom,
        datom_to_audit_entry,
        make_audit_handler,
        verify_chain,
    )
    # Construct a minimal AuditEntry to sanity-check the re-export.
    e = AuditEntry(
        id="sha256:abc", prev_hash=None, op="llm/call", args_hash="sha256:d",
        verdict="ok", latency_ms=1, recorded_at=1_712_000_000.0,
    )
    assert e.op == "llm/call"


def test_handler_factories_importable_from_package():
    from persistence.effect import (
        make_cache_handler,
        make_dry_run_handler,
        make_echo_llm_handler,
        make_fixed_clock_handler,
        make_pii_redact_handler,
        make_policy_handler,
        make_rate_limit_handler,
        make_retry_handler,
        make_system_clock_handler,
    )
    # Each factory is callable.
    for fn in (
        make_cache_handler, make_dry_run_handler, make_echo_llm_handler,
        make_fixed_clock_handler, make_pii_redact_handler, make_policy_handler,
        make_rate_limit_handler, make_retry_handler, make_system_clock_handler,
    ):
        assert callable(fn), fn


def test_verdicts_importable_from_package():
    from persistence.effect import (
        EDN_VERDICTS,
        PYTHON_VERDICTS,
        verdict_as_edn,
        verdict_as_python,
    )
    assert "allow" in PYTHON_VERDICTS
    assert ":allow" in EDN_VERDICTS
    assert verdict_as_edn("allow") == ":allow"
    assert verdict_as_python(":allow") == "allow"


def test_canonical_helpers_importable_from_package():
    from persistence.effect import canonical_dumps, canonical_hash
    assert canonical_dumps({"a": 1}) == '{"a":1}'
    assert canonical_hash({"a": 1}).startswith("sha256:")


def test_catalog_importable_from_package():
    import pytest

    from persistence.effect import CATALOG, OP_NAMES, validate_args
    assert "llm/call" in OP_NAMES
    assert "llm/call" in CATALOG
    # smoke: raises on unknown op
    with pytest.raises(KeyError):
        validate_args("nope", {})


def test_all_names_are_actually_attributes():
    """Every name in ``__all__`` must resolve on the package, so ``from
    persistence.effect import *`` surfaces each name cleanly.
    """
    import persistence.effect as pe
    missing = [name for name in pe.__all__ if not hasattr(pe, name)]
    assert not missing, f"names in __all__ not found on package: {missing}"


def test_all_contains_core_expected_names():
    import persistence.effect as pe
    expected = {
        "perform", "Runtime", "Handler", "with_runtime", "mask", "named",
        "AuditEntry", "make_audit_handler", "verify_chain",
        "make_policy_handler", "PolicyDenied",
        "verdict_as_edn", "verdict_as_python",
    }
    missing = expected - set(pe.__all__)
    assert not missing, f"__all__ missing expected names: {sorted(missing)}"
