"""ARIS Round 6 R6 N1 + R6-G2 — pin the canonicalisation drift invariant.

Convergent R1+R3 Round-6 finding: ``_canonicalise_content`` (the
dict-level helper used by ``make_audit_handler`` to pre-canonicalise
content before hashing) and ``AuditEntry.__post_init__`` (the
dataclass-level canonicaliser that ``to_dict`` reflects) are two
parallel canonicalisers that must stay in byte-lockstep. At HEAD they
are bit-equivalent, but no test pinned the invariant — so a future
field addition (or a change to only one of the two rules) would break
``verify_chain`` silently on the factory path without lighting up any
test.

This module pins the invariant two ways:

1. **Direct hash invariant** (R6-G2) — for every representative content
   shape, ``_content_hash(_canonicalise_content(content))`` must equal
   ``_content_hash(to_dict_without_id(AuditEntry(id=dummy, **content)))``
   byte-for-byte.

2. **Canonical-shape equivalence** (R6 N1) — the dict returned by
   ``_canonicalise_content(content)`` must equal the dict returned by
   ``to_dict_without_id(AuditEntry(id=dummy, **content))`` exactly
   (same keys, same values, same types).

The test matrix spans the three canonicalised slots (``policy_id``,
``handler_chain``, ``principal``) with the inputs production actually
emits (bare) and the inputs wire round-trips produce (pre-keyworded,
multi-colon).
"""
from __future__ import annotations

import uuid

import pytest

from persistence.effect.handlers.audit import (
    AuditEntry,
    _canonicalise_content,
    _content_hash,
)


_DUMMY_ID = "sha256:" + "0" * 64


def _base_content(**overrides) -> dict:
    """The exact shape ``make_audit_handler`` passes to ``_content_hash``.

    Matches ``src/persistence/effect/handlers/audit.py:417-431``.
    """
    content = dict(
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=42,
        recorded_at=1_700_000_000.0,
        result_hash="sha256:" + "c" * 64,
        error=None,
        policy_id=None,
        handler_chain=(),
        principal={},
        run_id=str(uuid.uuid4()),
        parent=None,
    )
    content.update(overrides)
    return content


def _dataclass_canonical(content: dict) -> dict:
    """Canonical content as seen through the dataclass side."""
    entry = AuditEntry(id=_DUMMY_ID, **content)
    d = entry.to_dict()
    d.pop("id")
    return d


# The matrix of content shapes that exercise every canonicalised slot.
# Each case simultaneously varies at least one canonicalised field and
# keeps the non-canonical fields fixed so failure localisation is easy.
_CASES = [
    pytest.param(
        _base_content(),
        id="all-defaults",
    ),
    # --- policy_id arms -------------------------------------------------
    pytest.param(
        _base_content(policy_id="bankability-v3"),
        id="policy-id-bare-production-shape",
    ),
    pytest.param(
        _base_content(policy_id=":bankability-v3"),
        id="policy-id-pre-keyworded",
    ),
    pytest.param(
        _base_content(policy_id="::bankability-v3"),
        id="policy-id-double-colon",
    ),
    pytest.param(
        _base_content(policy_id="unknown"),
        id="policy-id-unknown-default",
    ),
    # --- handler_chain arms --------------------------------------------
    pytest.param(
        _base_content(handler_chain=("audit", "llm", "tool")),
        id="handler-chain-bare-production-shape",
    ),
    pytest.param(
        _base_content(handler_chain=(":audit", ":llm", ":tool")),
        id="handler-chain-pre-keyworded",
    ),
    pytest.param(
        _base_content(handler_chain=(":audit", "llm", "::tool")),
        id="handler-chain-mixed-plus-double-colon",
    ),
    # --- principal arms ------------------------------------------------
    pytest.param(
        _base_content(principal={"user": "a", "tenant": "b"}),
        id="principal-bare-keys",
    ),
    pytest.param(
        _base_content(principal={":user": "a", ":tenant": "b"}),
        id="principal-pre-keyworded-keys",
    ),
    pytest.param(
        _base_content(principal={":user": "a", "tenant": "b", "::role": "x"}),
        id="principal-mixed-plus-double-colon",
    ),
    # --- all three arms bare simultaneously (R1 R6 N1 reproducer) ------
    pytest.param(
        _base_content(
            policy_id="bankability-v3",
            handler_chain=("audit", "llm", "tool"),
            principal={"user": "u1", "tenant": "t1"},
        ),
        id="three-arms-bare-production-shape",
    ),
    # --- all three arms pre-keyworded simultaneously -------------------
    pytest.param(
        _base_content(
            policy_id=":bankability-v3",
            handler_chain=(":audit", ":llm", ":tool"),
            principal={":user": "u1", ":tenant": "t1"},
        ),
        id="three-arms-pre-keyworded",
    ),
]


@pytest.mark.parametrize("content", _CASES)
def test_helper_and_dataclass_produce_identical_canonical_dict(content):
    """R6 N1: the dict-side helper and the dataclass-side
    ``__post_init__`` must produce equal canonical dicts.

    If a future contributor adds a new canonicalisation rule to only
    one of the two paths, this test lights up before the factory-path
    ``verify_chain`` silently breaks in production.
    """
    helper_out = _canonicalise_content(content)
    dataclass_out = _dataclass_canonical(content)
    assert helper_out == dataclass_out, (
        "Drift between _canonicalise_content and AuditEntry.__post_init__. "
        f"helper={helper_out!r} dataclass={dataclass_out!r}"
    )


@pytest.mark.parametrize("content", _CASES)
def test_helper_and_dataclass_produce_identical_content_hash(content):
    """R6-G2: the byte-identical hash invariant.

    This is the load-bearing assertion: ``make_audit_handler`` hashes
    ``_canonicalise_content(content)`` and stores that as ``entry.id``;
    ``verify_chain`` rehashes ``entry.to_dict() - {id}``. These two
    hashes must be byte-identical or the Merkle chain breaks.
    """
    helper_hash = _content_hash(_canonicalise_content(content))
    dataclass_hash = _content_hash(_dataclass_canonical(content))
    assert helper_hash == dataclass_hash, (
        f"Content hash drift: helper={helper_hash} dataclass={dataclass_hash}"
    )


def test_canonicalisation_is_idempotent_under_helper():
    """Passing already-canonical content through the helper a second
    time must be a no-op — the canonical form is a fixed point.
    """
    content = _base_content(
        policy_id="::bankability-v3",
        handler_chain=(":audit", "::llm"),
        principal={":user": "u", "::role": "r"},
    )
    once = _canonicalise_content(content)
    twice = _canonicalise_content(once)
    assert once == twice


def test_canonicalisation_is_idempotent_under_dataclass():
    """Reconstructing an ``AuditEntry`` from its own ``to_dict()`` must
    yield an equal entry — idempotence of ``__post_init__``.
    """
    content = _base_content(
        policy_id="::bankability-v3",
        handler_chain=(":audit", "::llm"),
        principal={":user": "u", "::role": "r"},
    )
    entry1 = AuditEntry(id=_DUMMY_ID, **content)
    d = entry1.to_dict()
    d.pop("id")
    entry2 = AuditEntry(id=_DUMMY_ID, **d)
    assert entry1 == entry2


def test_helper_preserves_unknown_keys_unchanged():
    """R6 N1 protection against *future* field additions: if someone
    adds a new field to ``AuditEntry`` but forgets to teach
    ``_canonicalise_content`` about it, the helper must at minimum not
    corrupt the unknown key — it passes through untouched.

    This is a softer guarantee than full equivalence (that's covered
    above), but it catches the "silent corruption on unknown key"
    failure mode that the direct-hash test cannot observe on a shape
    ``AuditEntry`` doesn't know about yet.
    """
    content = _base_content()
    content["_future_field"] = ":keyworded-value"
    out = _canonicalise_content(content)
    assert out["_future_field"] == ":keyworded-value"
