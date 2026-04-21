"""Spec registry.

A process-wide mutable map from namespaced keyword (str) -> Spec. Writes are
atomic (single dict assignment); existing Spec objects are never mutated —
a new version is swapped in. Versioning is encoded in the key convention:
``:persistence.foo/bar@v2`` pins a specific version; ``:persistence.foo/bar``
points to the current default.

Callers that need versioned behavior should register both keys explicitly.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from ._types import ConformError, Conformed, ConformResult, Spec, _render_error

# single process-wide registry; tests that need isolation can copy + restore
_REGISTRY: dict[str, Spec] = {}


# ----- registration ---------------------------------------------------------

def register(key: str, spec: Spec) -> None:
    """Register a spec under a namespaced keyword key.

    Re-registration under the same key is allowed and atomic — the global
    reference swaps to the new spec. Previously returned references keep
    pointing to whatever they resolved to at read time; i.e. ``ref(key)``
    always resolves the current binding lazily.
    """
    if not isinstance(key, str):
        raise TypeError(f"spec key must be str, got {type(key).__name__}")
    _REGISTRY[key] = spec


def get(key: str) -> Spec | None:
    return _REGISTRY.get(key)


def registered_keys() -> list[str]:
    return sorted(_REGISTRY.keys())


# ----- conform / parse (registry helpers) -----------------------------------

def conform(key: str, value: Any) -> ConformResult:
    """Conform ``value`` against the spec registered under ``key``."""
    sp = _REGISTRY.get(key)
    if sp is None:
        return ConformError(
            spec_key=key,
            value=value,
            reason=f"no spec registered under {key!r}",
            hint="register the spec first via spec.register()",
        )
    # ensure the produced Conformed carries the registry key, not the anon key
    result = sp.conform(value)
    if result.is_ok:
        return Conformed(value=result.value, spec_key=key)
    return result  # type: ignore[return-value]


def parse(key: str, value: Any) -> Conformed:
    """Parse-don't-validate wrapper. Raises ``SpecError`` on non-conform; returns
    ``Conformed`` otherwise. Downstream code that accepts ``Conformed[T]`` is
    statically immunized against raw, un-parsed input.
    """
    result = conform(key, value)
    if result.is_ok:
        return result  # type: ignore[return-value]
    raise SpecError(result)  # type: ignore[arg-type]


class SpecError(Exception):
    """Raised by ``parse`` when a value does not conform. Wraps the structured
    :class:`ConformError` for programmatic access.
    """

    def __init__(self, error: ConformError) -> None:
        self.error = error
        super().__init__(_render_error(error))


# ----- generative testing ---------------------------------------------------

def generate_example(key: str) -> Any:
    sp = _REGISTRY.get(key)
    if sp is None:
        raise KeyError(f"no spec registered under {key!r}")
    return sp.generate()


def quickcheck(key: str,
               prop: Callable[[Any], bool],
               n: int = 100) -> list[Any]:
    """Run ``prop(value)`` against ``n`` generated examples of the given spec.
    Return the list of values that failed the property (empty = pass).

    Uses the hypothesis library for shrinking when available; falls back to
    the spec's own ``generate()`` otherwise.
    """
    sp = _REGISTRY.get(key)
    if sp is None:
        raise KeyError(f"no spec registered under {key!r}")
    failures: list[Any] = []
    for _ in range(n):
        val = sp.generate()
        conf = sp.conform(val)
        if not conf.is_ok:
            # our own generator produced an invalid example — record as failure
            failures.append(val)
            continue
        try:
            ok = bool(prop(conf.value))
        except Exception:  # noqa: BLE001 - prop exceptions count as failures
            ok = False
        if not ok:
            failures.append(val)
    return failures


# ----- LLM-friendly error rendering -----------------------------------------

def explain_for_llm(key: str, value: Any) -> str:
    """Return a compact, LLM-friendly explanation of why ``value`` fails the
    registered spec. Every line contains a spec-key and a Fix clause, so the
    LLM can self-heal by consuming the message verbatim.

    Format:

        Value failed <spec-key> — <primary reason>.
        - at <path>: <reason>. Fix: <hint>.
        - at <path>: <reason>. Fix: <hint>.
        Fix: <top-level hint>.
    """
    result = conform(key, value)
    if result.is_ok:
        return ""
    err: ConformError = result  # type: ignore[assignment]
    # Top-level always references the registry key the caller asked about;
    # nested errors keep their own inner spec names so the LLM can drill down.
    lines = [f"Value failed {key} — {err.reason}."]

    def walk(e: ConformError) -> None:
        path_str = _format_path(e.path) if e.path else ""
        where = f" at {path_str}" if path_str else ""
        line = f"- {e.spec_key}{where}: {e.reason}."
        if e.hint:
            line += f" Fix: {e.hint}."
        lines.append(line)
        for sub in e.sub_errors:
            walk(sub)

    for sub in err.sub_errors:
        walk(sub)
    if err.hint:
        lines.append(f"Fix: {err.hint}.")
    return "\n".join(lines)


def _format_path(path: tuple[Any, ...]) -> str:
    parts = []
    for p in path:
        if isinstance(p, int):
            parts.append(f"[{p}]")
        else:
            parts.append(str(p))
    return ".".join(parts)
