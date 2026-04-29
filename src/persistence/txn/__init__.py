"""persistence.txn — atomic multi-datom commit + snapshot isolation.

See ``docs/plans/2026-04-27-v0.5-txn-design.md`` for the full design.
"""

__version__ = "0.5.2"

from persistence.txn._commute import (
    lookup_commute,
    register_commute,
    unregister_commute,
)
from persistence.txn.atom import Atom
from persistence.txn.errors import (
    AtomCASExhausted,
    AtomInDosyncProhibited,
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
    "Atom",
    "AtomCASExhausted",
    "AtomInDosyncProhibited",
    "EffectInIoBlock",
    "freeze",
    "is_immutable_value",
    "lookup_commute",
    "NestedDosyncNotSupported",
    "Ref",
    "RefBranchMismatch",
    "RefValueNotImmutable",
    "register_commute",
    "Transaction",
    "TxnDeadlineExceeded",
    "TxnError",
    "TxnRetryExhausted",
    "unregister_commute",
    "__version__",
]


# ---------------------------------------------------------------------------
# Spec registration runs at import time so any module that imports
# persistence.txn picks up the boundary contracts without needing a
# separate setup call. Re-registration is idempotent.
# ---------------------------------------------------------------------------
from persistence.txn._specs import register_txn_specs as _register_txn_specs  # noqa: E402

_register_txn_specs()
