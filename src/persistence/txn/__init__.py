"""persistence.txn — atomic multi-datom commit + snapshot isolation.

See ``docs/plans/2026-04-27-v0.5-txn-design.md`` for the full design.
"""

__version__ = "0.5.0a1"

from persistence.txn.errors import (
    EffectInIoBlock,
    NestedDosyncNotSupported,
    RefBranchMismatch,
    RefValueNotImmutable,
    TxnDeadlineExceeded,
    TxnError,
    TxnRetryExhausted,
)
from persistence.txn.ref import Ref, freeze, is_immutable_value
from persistence.txn.transaction import Transaction

__all__ = [
    "EffectInIoBlock",
    "freeze",
    "is_immutable_value",
    "NestedDosyncNotSupported",
    "Ref",
    "RefBranchMismatch",
    "RefValueNotImmutable",
    "TxnDeadlineExceeded",
    "TxnError",
    "TxnRetryExhausted",
    "Transaction",
    "__version__",
]


# ---------------------------------------------------------------------------
# Spec registration runs at import time so any module that imports
# persistence.txn picks up the boundary contracts without needing a
# separate setup call. Re-registration is idempotent.
# ---------------------------------------------------------------------------
from persistence.txn._specs import register_txn_specs as _register_txn_specs  # noqa: E402

_register_txn_specs()
