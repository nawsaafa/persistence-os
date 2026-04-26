"""persistence.txn — atomic multi-datom commit + snapshot isolation.

See ``docs/plans/2026-04-27-v0.5-txn-design.md`` for the full design.
"""
from persistence.txn.errors import (
    EffectInIoBlock,
    NestedDosyncNotSupported,
    RefBranchMismatch,
    RefValueNotImmutable,
    TxnDeadlineExceeded,
    TxnError,
    TxnRetryExhausted,
)

__version__ = "0.5.0a1"

__all__ = [
    "EffectInIoBlock",
    "NestedDosyncNotSupported",
    "RefBranchMismatch",
    "RefValueNotImmutable",
    "TxnDeadlineExceeded",
    "TxnError",
    "TxnRetryExhausted",
    "__version__",
]
