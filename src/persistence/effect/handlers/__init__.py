"""persistence.effect.handlers — the handler library.

Each handler is a factory returning a :class:`persistence.effect.runtime.Handler`.
Per spec §9, handlers carry their own ctx (no hidden globals) and route all
non-determinism through effects (:clock/now, :random).
"""
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.anthropic import make_anthropic_llm_handler
from persistence.effect.handlers.claude_code import make_claude_code_llm_handler
from persistence.effect.handlers.fs import make_fs_handler, FsCapabilityDenied  # noqa: F401
from persistence.effect.handlers.shell import (  # noqa: F401
    ALLOWLIST_V1, ALLOWLIST_VERSION, ENV_DEFAULT,
    ShellAllowlistDenied, ShellAllowlistVersionMismatch,
    make_shell_handler,
)
from persistence.effect.handlers.code import make_code_run_dispatch_handler  # noqa: F401
from persistence.effect.handlers.git import make_git_handler, GitArgValidation  # noqa: F401

__all__ = [
    "make_callable_llm_handler",
    "make_anthropic_llm_handler",
    "make_claude_code_llm_handler",
    "make_fs_handler",
    "FsCapabilityDenied",
    "ALLOWLIST_V1",
    "ALLOWLIST_VERSION",
    "ENV_DEFAULT",
    "ShellAllowlistDenied",
    "ShellAllowlistVersionMismatch",
    "make_shell_handler",
    "make_code_run_dispatch_handler",
    "make_git_handler",
    "GitArgValidation",
]
