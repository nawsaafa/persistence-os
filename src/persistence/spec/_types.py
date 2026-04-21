"""Core types for the Spec module: Spec, Conformed, ConformError.

Parse-don't-validate layer. A `Spec` is a predicate-generator pair. `conform`
returns either a `Conformed` (the refined value in a typed container) or a
`ConformError` (a structured failure, never a raw exception on the happy path).
Downstream business logic type-checks the result and therefore cannot
accidentally consume raw, unvalidated input.

The discriminated union is expressed as two frozen dataclasses with a shared
`is_ok` attribute — avoiding `isinstance` ladders in callers.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

# ----- a universal "missing" sentinel; distinct from None so None can be a valid value
_MISSING = object()

T = TypeVar("T")


@dataclass(frozen=True)
class Conformed(Generic[T]):
    """Successful conform result — a typed container wrapping the refined value.

    The container is deliberately opaque: downstream code must call ``unwrap()``
    or access ``value`` explicitly. This enforces the parse-don't-validate
    boundary — no function that accepts ``int`` will accidentally accept a
    ``Conformed[int]``.
    """

    value: T
    spec_key: str | None = None  # namespaced keyword of the spec that produced this

    @property
    def is_ok(self) -> bool:
        return True

    def unwrap(self) -> T:
        return self.value


@dataclass(frozen=True)
class ConformError:
    """Structured conformance failure. Carries enough detail to render an
    LLM-friendly explanation via ``spec.explain_for_llm``.

    - ``spec_key``: the namespaced keyword (or free-form identifier) of the spec
    - ``value``: the offending input (verbatim, may be large — caller truncates)
    - ``reason``: short human string, one sentence
    - ``path``: keys/indices walked to reach the failure (e.g. ["facts", 3, "e"])
    - ``hint``: self-healing advice ("provide a non-empty string") — optional
    - ``sub_errors``: for combinators like ``or``/``keys``, the child failures
    """

    spec_key: str | None
    value: Any
    reason: str
    path: tuple[Any, ...] = field(default_factory=tuple)
    hint: str | None = None
    sub_errors: tuple["ConformError", ...] = field(default_factory=tuple)

    @property
    def is_ok(self) -> bool:
        return False

    def with_path(self, *crumbs: Any) -> "ConformError":
        """Prepend path crumbs (used by container combinators to add context)."""
        return dataclasses.replace(self, path=tuple(crumbs) + self.path)


#: discriminated union alias used across the module
ConformResult = Conformed[T] | ConformError


# ----- the Spec abstraction --------------------------------------------------

class Spec:
    """A predicate-generator pair.

    Subclasses implement ``_conform`` (the predicate-refinement) and ``_generate``
    (produces an example value). Callers interact via ``conform``, ``generate``,
    ``explain`` — which provide the stable public contract.

    Specs are values: two specs with identical internal state should ``__eq__``,
    and all built-in combinators produce frozen dataclasses so equality + hashing
    is structural. This is the "EDN data" property from paper §5.6.
    """

    #: short identifier used in error messages; combinators override
    spec_name: str = "spec"

    def conform(self, value: Any) -> ConformResult:
        try:
            return self._conform(value)
        except Exception as exc:  # noqa: BLE001 - intentional: unexpected exceptions
            # a predicate raising is itself a conform failure; never leak to caller
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"predicate raised {type(exc).__name__}: {exc}",
            )

    # ----- abstract

    def _conform(self, value: Any) -> ConformResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def _generate(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    # ----- public helpers

    def generate(self) -> Any:
        return self._generate()

    def explain(self, value: Any) -> str:
        """Return a human-readable explanation of why ``value`` fails, or ``""``
        if it conforms."""
        result = self.conform(value)
        if result.is_ok:
            return ""
        return _render_error(result)  # type: ignore[arg-type]

    def to_edn(self) -> Any:
        """EDN/JSON-compatible serialization. Concrete specs override; default
        returns the spec name as a keyword-like string."""
        return {":spec": self.spec_name}

    def __repr__(self) -> str:
        return f"<Spec {self.spec_name}>"


# ----- rendering -------------------------------------------------------------

def _truncate(v: Any, limit: int = 80) -> str:
    s = repr(v)
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def _render_error(err: ConformError, indent: int = 0) -> str:
    prefix = "  " * indent
    key = err.spec_key or "<anon>"
    path = "".join(f"[{p!r}]" for p in err.path)
    body = f"{prefix}{key}{path}: {err.reason} (got {_truncate(err.value)})"
    if err.hint:
        body += f"\n{prefix}  Fix: {err.hint}"
    if err.sub_errors:
        body += "\n" + "\n".join(_render_error(e, indent + 1) for e in err.sub_errors)
    return body


# ----- helper used by several combinators -----------------------------------

PredicateFn = Callable[[Any], bool]
GeneratorFn = Callable[[], Any]
