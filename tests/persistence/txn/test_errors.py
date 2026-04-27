"""Error hierarchy for persistence.txn — Phase A."""
from persistence.txn.errors import (
    TxnError,
    TxnRetryExhausted,
    TxnDeadlineExceeded,
    RefBranchMismatch,
    RefValueNotImmutable,
    EffectInIoBlock,
    NestedDosyncNotSupported,
)


def test_all_errors_subclass_TxnError():
    for cls in (
        TxnRetryExhausted,
        TxnDeadlineExceeded,
        RefBranchMismatch,
        RefValueNotImmutable,
        EffectInIoBlock,
        NestedDosyncNotSupported,
    ):
        assert issubclass(cls, TxnError), f"{cls.__name__} must subclass TxnError"


def test_TxnError_subclasses_Exception():
    assert issubclass(TxnError, Exception)
