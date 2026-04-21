"""Verdict vocabulary reconciler.

ARIS R3 F6 identified three disagreeing verdict vocabularies:

    policy_eval.evaluate()  →  {"allow","deny","deny-silently","require-approval"}
    AuditEntry.verdict      →  {"ok","error","deny","deny-silently","require-approval"}
    :audit/verdict spec     →  {":allow",":deny",":deny-silently",":require-approval",
                                ":ok",":error"}

The runtime (policy, audit) keeps bare Python strings — that's the canonical
form inside the process and is what every existing unit test asserts. The
canonical spec uses EDN keywords because that's the wire form. This module
is the single source of truth for translating between the two.

Semantics
---------

- ``PYTHON_VERDICTS`` — the full set of strings the policy + audit runtimes
  may emit, including the audit handler's ``"ok"``/``"error"`` result path.
- ``EDN_VERDICTS`` — the spec-side keyword form (leading colon).
- :func:`as_edn` — ``"allow" → ":allow"``; idempotent on already-keyworded
  inputs.
- :func:`as_python` — the inverse.

Invariants
----------

- ``as_python(as_edn(v)) == v`` for every v in ``PYTHON_VERDICTS``.
- ``as_edn(as_python(v)) == v`` for every v in ``EDN_VERDICTS``.

Callers
-------

- :func:`persistence.effect.handlers.audit.audit_entry_to_datom` calls
  :func:`as_edn` when emitting ``:audit/verdict`` into a Fact-spec datom so
  the boundary conforms.
- :func:`persistence.effect.handlers.audit.datom_to_audit_entry` calls
  :func:`as_python` when restoring the in-memory :class:`AuditEntry`.
"""
from __future__ import annotations

#: All verdict strings the policy + audit runtimes may emit.
PYTHON_VERDICTS = frozenset(
    {
        "allow",
        "deny",
        "deny-silently",
        "require-approval",
        # Audit handler success/failure paths — these never come from the
        # policy evaluator but still land in :audit/verdict.
        "ok",
        "error",
    }
)

#: The EDN wire form of every :data:`PYTHON_VERDICTS` string.
EDN_VERDICTS = frozenset({":" + v for v in PYTHON_VERDICTS})


def as_edn(verdict: str) -> str:
    """Return the EDN-keyword form of ``verdict``.

    Idempotent: already-keyworded inputs (``":allow"``) pass through.
    Raises :class:`ValueError` on any unknown vocabulary to fail loudly
    when a new verdict is introduced without updating this module.
    """
    if verdict.startswith(":"):
        if verdict not in EDN_VERDICTS:
            raise ValueError(f"unknown EDN verdict: {verdict!r}")
        return verdict
    if verdict not in PYTHON_VERDICTS:
        raise ValueError(f"unknown Python verdict: {verdict!r}")
    return ":" + verdict


def as_python(verdict: str) -> str:
    """Return the Python-string form of ``verdict``.

    Idempotent on non-keyworded inputs. Raises :class:`ValueError` on any
    unknown vocabulary.
    """
    if verdict.startswith(":"):
        stripped = verdict[1:]
        if stripped not in PYTHON_VERDICTS:
            raise ValueError(f"unknown EDN verdict: {verdict!r}")
        return stripped
    if verdict not in PYTHON_VERDICTS:
        raise ValueError(f"unknown Python verdict: {verdict!r}")
    return verdict


__all__ = ["PYTHON_VERDICTS", "EDN_VERDICTS", "as_edn", "as_python"]
