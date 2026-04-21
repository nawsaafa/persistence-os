"""Interceptor adapters for legacy memory systems.

Each adapter wraps a legacy client (mem0, Kuzu, raw Postgres writer) and
ensures every mutation is first appended to the :mod:`persistence.fact`
datom log as a bitemporal 8-tuple, then — and only then — is the legacy
write performed.

The wiring pattern, rollback procedure, and VPS test plan for the Memory
Palace integration are in ``docs/memory-palace-integration.md``.
"""

from persistence.fact.interceptors.mem0_adapter import (
    InterceptorError,
    Mem0Interceptor,
)

__all__ = ["InterceptorError", "Mem0Interceptor"]
