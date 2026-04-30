"""``Substrate`` — curated namespace + lifecycle facade (SDK1 placeholder).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-1, ``Substrate`` is a thin curated namespace over the seven
Persistence OS modules — NOT a god-object wrapper. The full body
(``open()``, ``close()``, ``__enter__``/``__exit__``, the seven module
attribute getters, and the ``health_check`` / ``version_info`` /
``module_status`` lifecycle helpers) lands in SDK2.

SDK1 ships an empty placeholder so that the public-surface skeleton is
importable from day one — ``from persistence.sdk import Substrate`` must
work after SDK1 even though no method body is filled in. The class is
decorated ``@experimental`` (rather than ``@stable("v0.8")``) until SDK2
finishes the body and the spec generator picks it up; this keeps the
contract honest. SDK2 swaps the decorator to ``@stable("v0.8")`` along
with the body fill-in.
"""
from __future__ import annotations

from dataclasses import dataclass

from persistence.sdk._stability import experimental


@experimental(
    reason="SDK1 placeholder; SDK2 fills in lifecycle + module attributes "
    "and promotes to @stable(\"v0.8\").",
)
@dataclass(frozen=True)
class Substrate:
    """Curated namespace + lifecycle facade for the v0.8 adapter contract.

    SDK1 ships this class as an empty placeholder so the public surface
    can be imported immediately. SDK2 will populate the lifecycle methods
    (``open``, ``close``, context manager) and the seven module attribute
    getters (``fact`` / ``effect`` / ``plan`` / ``replay`` / ``txn`` /
    ``spec`` / ``repl``) per ADR-1.

    Adapter authors who need to bind code today should pin to
    ``persistence.sdk.open_store`` for backend-URI dispatch and to the
    ``@stable("v0.8")`` decorator metadata; the ``Substrate`` class
    itself is not contract surface until SDK2 promotes it.
    """
