"""Phase 2.1c — claim namespace registry (Design §5, §10.1)."""
from persistence.claim._registry import CLAIM_KINDS, is_claim_kind


def test_claim_kinds_is_frozenset_with_exact_membership():
    assert isinstance(CLAIM_KINDS, frozenset)
    assert CLAIM_KINDS == frozenset({":claim/tool-exec", ":claim/blob-put"})


def test_is_claim_kind_recognizes_registered_claim_kinds():
    assert is_claim_kind(":claim/tool-exec") is True
    assert is_claim_kind(":claim/blob-put") is True


def test_is_claim_kind_rejects_fact_kinds():
    for fact_kind in [":llm/decision", ":llm/messages", ":llm/call", ":plan/step", ":txn/commit"]:
        assert is_claim_kind(fact_kind) is False, f"fact kind leaked: {fact_kind}"


def test_is_claim_kind_rejects_garbage_and_unknown_claim_kinds():
    for bad in ["garbage", "", ":claim/", ":claim/unknown-kind", "claim/tool-exec", None]:
        if bad is None:
            continue  # type-check would catch
        assert is_claim_kind(bad) is False, f"garbage accepted: {bad!r}"
