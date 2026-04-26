"""Spec registry contains :persistence.txn/* attributes after module import."""
import persistence.txn  # noqa: F401 — triggers registration
from persistence.spec import registered_keys


REQUIRED_KEYS = [
    ":persistence.txn/commit-id",
    ":persistence.txn/started-at",
    ":persistence.txn/committed-at",
    ":persistence.txn/retry-count",
    ":persistence.txn/read-set",
    ":persistence.txn/non-deterministic-retry",
    ":persistence.txn/intent-log",
    ":persistence.txn/commit",
]


def test_all_txn_specs_registered_after_import():
    keys = registered_keys()
    for key in REQUIRED_KEYS:
        assert key in keys, f"missing spec registration for {key!r}"


def test_commit_id_spec_conforms_uuid_string():
    from persistence.spec import conform
    result = conform(":persistence.txn/commit-id", "550e8400-e29b-41d4-a716-446655440000")
    assert result.is_ok


def test_commit_id_spec_rejects_non_uuid():
    from persistence.spec import conform
    result = conform(":persistence.txn/commit-id", "not-a-uuid")
    assert not result.is_ok


def test_retry_count_spec_conforms_non_negative_int():
    from persistence.spec import conform
    assert conform(":persistence.txn/retry-count", 0).is_ok
    assert conform(":persistence.txn/retry-count", 5).is_ok
    assert not conform(":persistence.txn/retry-count", -1).is_ok
    assert not conform(":persistence.txn/retry-count", "5").is_ok
