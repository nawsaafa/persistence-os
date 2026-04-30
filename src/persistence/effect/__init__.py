"""persistence.effect — algebraic effect handler stack.

See docs/agent3-effect-spec.md for the architectural spec.
See paper §4.2 for the formal definition.

Public surface (ARIS R3 F7): the core types/functions from
:mod:`persistence.effect.runtime`, the handler factories from
:mod:`persistence.effect.handlers`, the canonical-JSON helpers from
:mod:`persistence.effect.canonical`, the op catalog from
:mod:`persistence.effect.catalog`, and the verdict reconciler from
:mod:`persistence.effect.verdicts`. Consumers should import from
``persistence.effect`` directly rather than reaching into submodules.
"""

from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.effect.catalog import CATALOG, OP_NAMES, validate_args
from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    datom_to_audit_entry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.cache import make_cache_handler
from persistence.effect.handlers.clock import (
    make_fixed_clock_handler,
    make_replay_clock_handler,
    make_system_clock_handler,
)
from persistence.effect.handlers.code import (
    CodeExecError,
    CodeExecForbiddenImport,
    CodeExecMemoryExceeded,
    CodeExecOutsideDosync,
    CodeExecReplayMismatch,
    CodeExecResult,
    CodeExecTimeout,
    exec_code,
    make_code_exec_handler,
)
from persistence.effect.handlers.dry_run import make_dry_run_handler
from persistence.effect.handlers.pii_redact import make_pii_redact_handler
from persistence.effect.handlers.policy import (
    ApprovalRequired,
    PolicyDenied,
    make_policy_handler,
)
from persistence.effect.handlers.rate_limit import make_rate_limit_handler
from persistence.effect.handlers.raw import (
    TransientError,
    make_echo_llm_handler,
    make_flaky_llm_handler,
    make_random_handler,
    make_scripted_tool_handler,
)
from persistence.effect.handlers.retry import make_retry_handler
from persistence.effect.policy_eval import PolicyError, evaluate as evaluate_policy
from persistence.effect.runtime import (
    Effect,
    Handler,
    Runtime,
    Unhandled,
    mask,
    named,
    perform,
    with_runtime,
)
from persistence.effect.verdicts import (
    EDN_VERDICTS,
    PYTHON_VERDICTS,
    as_edn as verdict_as_edn,
    as_python as verdict_as_python,
)

__all__ = [
    # core runtime
    "Effect",
    "Handler",
    "Runtime",
    "Unhandled",
    "perform",
    "named",
    "mask",
    "with_runtime",
    # canonical json / hashing
    "canonical_dumps",
    "canonical_hash",
    # op catalog
    "CATALOG",
    "OP_NAMES",
    "validate_args",
    # audit
    "AuditEntry",
    "audit_entry_to_datom",
    "datom_to_audit_entry",
    "make_audit_handler",
    "verify_chain",
    # :code/exec sandbox (#141 / Phase 2.0b)
    "CodeExecError",
    "CodeExecForbiddenImport",
    "CodeExecMemoryExceeded",
    "CodeExecOutsideDosync",
    "CodeExecReplayMismatch",
    "CodeExecResult",
    "CodeExecTimeout",
    "exec_code",
    "make_code_exec_handler",
    # policy
    "ApprovalRequired",
    "PolicyDenied",
    "PolicyError",
    "evaluate_policy",
    "make_policy_handler",
    # handler factories (alphabetical)
    "make_cache_handler",
    "make_dry_run_handler",
    "make_echo_llm_handler",
    "make_fixed_clock_handler",
    "make_flaky_llm_handler",
    "make_pii_redact_handler",
    "make_random_handler",
    "make_rate_limit_handler",
    "make_replay_clock_handler",
    "make_retry_handler",
    "make_scripted_tool_handler",
    "make_system_clock_handler",
    # raw helpers
    "TransientError",
    # verdicts
    "EDN_VERDICTS",
    "PYTHON_VERDICTS",
    "verdict_as_edn",
    "verdict_as_python",
]
