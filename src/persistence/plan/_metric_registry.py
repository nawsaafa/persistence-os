"""Metric registry for `persistence.plan.optimize` (v0.6.0a1).

Per design §5 rule (2): metrics are registered by `(metric_id, version)`,
NOT by source hash or callable identity. The registry is the canonical
identity of a metric — callers cannot pass a raw callable into
`optimize()`; they pass a `MetricRef`, and the `(id, version)` pair
becomes part of the optimizer-call provenance hash.

This makes metric upgrades explicit (`v1 → v2` is a string change in the
provenance) and keeps the canonical hash deterministic across runs and
machines (no Python-callable hashing).

Design contract:

* `MetricRef` is a `NamedTuple` so it's hashable, structurally typed, and
  trivially serialized for provenance pinning.
* Registration collisions raise `ValueError` unless `replace=True`. The
  override exists for test fixtures and explicit metric-bump rituals.
* Misses raise `MetricNotRegistered` (a `KeyError` subclass — see
  `_errors.py`) — domain error, not raw `KeyError` leakage.
* `unregister_metric` is test-only convenience; production lifecycle is
  process-lifetime.

References:
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5 rule (2)
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A2
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, NamedTuple

from persistence.plan._errors import MetricNotRegistered

if TYPE_CHECKING:
    # Avoid runtime import cycle: _execute imports from this module via the
    # public re-export in __init__.py. ExecutionResult is only needed for
    # type-checker awareness on the registered callable signature.
    from persistence.plan._execute import ExecutionResult

__all__ = [
    "MetricRef",
    "lookup_metric",
    "register_metric",
    "unregister_metric",
]


class MetricRef(NamedTuple):
    """Stable identity of a metric in the registry.

    Two `MetricRef` instances compare equal iff both `id` and `version`
    match. Bumping `version` is the explicit signal that the metric
    semantics have changed — same `id` with new `version` is a distinct
    entry, NOT a silent overwrite.

    Attributes:
        id: Stable metric identifier (e.g. ``"exact-match"``,
            ``"f1-token-overlap"``). Plain string, no leading colon.
        version: Metric implementation version (e.g. ``"v1"``,
            ``"v2-2026-04"``). Bumped whenever the metric body changes.
    """

    id: str
    version: str


# Module-level registry. Keyed by `MetricRef` (hashable NamedTuple).
# Process-lifetime singleton: tests register fixtures + clean up via
# `unregister_metric` (idempotent guard in test code, not here).
_REGISTRY: dict[MetricRef, Callable[["ExecutionResult", dict], float]] = {}


def register_metric(
    ref: MetricRef,
    fn: Callable[["ExecutionResult", dict], float],
    *,
    replace: bool = False,
) -> None:
    """Bind a metric callable to ``ref`` in the registry.

    Args:
        ref: Stable `(id, version)` identity of the metric. Becomes part
            of the optimizer-call provenance hash when callers run
            `optimize()` with this metric.
        fn: Callable consuming ``(ExecutionResult, expected: dict) -> float``.
            The score is a scalar — higher is better by convention; the
            sign convention is the metric's own contract, not enforced
            here.
        replace: If False (default) and ``ref`` is already registered,
            raise ``ValueError``. If True, overwrite the existing entry.
            The override exists for test fixtures and explicit
            metric-bump rituals.

    Raises:
        ValueError: ``ref`` already registered and ``replace=False``.
            Message includes the offending ``MetricRef`` for grep-ability.
    """
    if not replace and ref in _REGISTRY:
        # Collision protection — silent overwrite would silently break
        # the provenance hash semantics (same `MetricRef`, different fn,
        # same hash). Demand explicit override.
        raise ValueError(
            f"MetricRef collision: {ref!r} is already registered. "
            f"Pass replace=True to override (test fixtures only)."
        )
    _REGISTRY[ref] = fn


def lookup_metric(ref: MetricRef) -> Callable[["ExecutionResult", dict], float]:
    """Return the callable registered for ``ref``.

    Args:
        ref: The `MetricRef` to resolve.

    Returns:
        The exact callable registered (identity, not copy). Caller can
        compare with `is` for equality.

    Raises:
        MetricNotRegistered: No entry for ``ref``. Subclass of
            ``KeyError`` so callers can ``except KeyError`` if they want
            the broader category.
    """
    try:
        return _REGISTRY[ref]
    except KeyError as exc:
        raise MetricNotRegistered(
            f"No metric registered for {ref!r}. Use register_metric() first."
        ) from exc


def unregister_metric(ref: MetricRef) -> None:
    """Remove ``ref`` from the registry. Test-only convenience.

    Args:
        ref: The `MetricRef` to remove.

    Raises:
        MetricNotRegistered: No entry for ``ref``. Symmetric with
            `lookup_metric` so misses are domain-typed everywhere.
    """
    try:
        del _REGISTRY[ref]
    except KeyError as exc:
        raise MetricNotRegistered(
            f"No metric registered for {ref!r}; nothing to unregister."
        ) from exc
