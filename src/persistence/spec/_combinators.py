"""Spec combinators.

Each combinator is a plain dataclass (frozen, hashable, structurally equal),
which gives us the "Specs are values" property for free — two equivalent specs
compare equal, can live in sets, and serialize via ``to_edn``.

Contract notes:
- ``and_`` is boolean-AND over predicates; refined value is the last-ok refinement.
- ``or_`` tries specs left-to-right; first Conformed wins.
- ``keys`` is an open shape (extra keys allowed) by Datomic convention.
- ``seq_of`` rejects strings even though they're iterable (common footgun).
- ``tuple_of`` is fixed-arity, heterogeneous.
- ``ref`` resolves lazily against the registry so specs can reference one another
  in either order during module load.
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field
from typing import Any, Mapping

from ._types import ConformError, Conformed, ConformResult, Spec

_rng = random.Random()


# ----- and_ -----------------------------------------------------------------

@dataclass(frozen=True)
class _And(Spec):
    members: tuple[Spec, ...]
    spec_name: str = ":persistence.spec/and"

    def _conform(self, value: Any) -> ConformResult:
        last_ok: Conformed | None = None
        for sp in self.members:
            result = sp.conform(value)
            if not result.is_ok:
                return result
            last_ok = result
            value = result.value  # refinement pipeline
        if last_ok is None:
            # empty and_ is trivially true
            return Conformed(value=value, spec_key=self.spec_name)
        return last_ok

    def _generate(self) -> Any:
        # Heuristic: generate from the first member, then check against the rest.
        # Retry up to 20 times. If that fails we return the first member's sample
        # unconditionally — generative testing callers will catch the mismatch.
        if not self.members:
            return None
        for _ in range(20):
            val = self.members[0].generate()
            if all(sp.conform(val).is_ok for sp in self.members[1:]):
                return val
        return self.members[0].generate()


def and_(*specs: Spec) -> Spec:
    return _And(members=tuple(specs))


# ----- or_ ------------------------------------------------------------------

@dataclass(frozen=True)
class _Or(Spec):
    members: tuple[Spec, ...]
    spec_name: str = ":persistence.spec/or"

    def _conform(self, value: Any) -> ConformResult:
        errs: list[ConformError] = []
        for sp in self.members:
            result = sp.conform(value)
            if result.is_ok:
                return result
            errs.append(result)  # type: ignore[arg-type]
        return ConformError(
            spec_key=self.spec_name,
            value=value,
            reason=f"matched none of {len(self.members)} alternatives",
            sub_errors=tuple(errs),
            hint="satisfy at least one of the alternatives",
        )

    def _generate(self) -> Any:
        if not self.members:
            return None
        return _rng.choice(self.members).generate()


def or_(*specs: Spec) -> Spec:
    return _Or(members=tuple(specs))


# ----- not_ -----------------------------------------------------------------

@dataclass(frozen=True)
class _Not(Spec):
    inner: Spec
    spec_name: str = ":persistence.spec/not"

    def _conform(self, value: Any) -> ConformResult:
        result = self.inner.conform(value)
        if result.is_ok:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"value matched inner spec {self.inner.spec_name}, expected non-match",
                hint="provide a value that does NOT conform to the inner spec",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self) -> Any:
        # No principled generation for negated specs; return a sentinel string
        # that is unlikely to match any reasonable inner spec, retry otherwise.
        candidates = ["__not_" + "".join(_rng.choices(string.ascii_letters, k=8)),
                      None, object()]
        for c in candidates:
            if not self.inner.conform(c).is_ok:
                return c
        return candidates[0]


def not_(spec: Spec) -> Spec:
    return _Not(inner=spec)


# ----- maybe (nilable) ------------------------------------------------------

@dataclass(frozen=True)
class _Maybe(Spec):
    inner: Spec
    spec_name: str = ":persistence.spec/maybe"

    def _conform(self, value: Any) -> ConformResult:
        if value is None:
            return Conformed(value=None, spec_key=self.spec_name)
        return self.inner.conform(value)

    def _generate(self) -> Any:
        if _rng.random() < 0.25:
            return None
        return self.inner.generate()


def maybe(spec: Spec) -> Spec:
    return _Maybe(inner=spec)


# ----- keys (map with keyed shape) ------------------------------------------

def _freeze_map(m: Mapping[Any, Spec] | None) -> tuple[tuple[Any, Spec], ...]:
    if m is None:
        return ()
    return tuple(sorted(m.items(), key=lambda kv: repr(kv[0])))


@dataclass(frozen=True)
class _Keys(Spec):
    """Map shape with required and optional keyed specs. Open by default (extra
    keys tolerated — EDN/Datomic convention).
    """

    required: tuple[tuple[Any, Spec], ...] = field(default_factory=tuple)
    optional: tuple[tuple[Any, Spec], ...] = field(default_factory=tuple)
    spec_name: str = ":persistence.spec/keys"

    def _conform(self, value: Any) -> ConformResult:
        if not isinstance(value, dict):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected map (dict), got {type(value).__name__}",
                hint="provide a dict matching the declared shape",
            )
        refined: dict[Any, Any] = dict(value)  # keep extras verbatim
        errors: list[ConformError] = []
        for k, sp in self.required:
            if k not in value:
                errors.append(ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason=f"missing required key {k!r}",
                    path=(k,),
                    hint=f"include {k!r} with a value conforming to {sp.spec_name}",
                ))
                continue
            sub = sp.conform(value[k])
            if not sub.is_ok:
                errors.append(sub.with_path(k))  # type: ignore[union-attr]
            else:
                refined[k] = sub.value  # type: ignore[union-attr]
        for k, sp in self.optional:
            if k in value:
                sub = sp.conform(value[k])
                if not sub.is_ok:
                    errors.append(sub.with_path(k))  # type: ignore[union-attr]
                else:
                    refined[k] = sub.value  # type: ignore[union-attr]
        if errors:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"{len(errors)} key(s) failed",
                sub_errors=tuple(errors),
                hint="fix each sub-error listed above",
            )
        return Conformed(value=refined, spec_key=self.spec_name)

    def _generate(self) -> dict[Any, Any]:
        out: dict[Any, Any] = {}
        for k, sp in self.required:
            out[k] = sp.generate()
        for k, sp in self.optional:
            if _rng.random() < 0.6:
                out[k] = sp.generate()
        return out


def keys(required: Mapping[Any, Spec] | None = None,
         optional: Mapping[Any, Spec] | None = None) -> Spec:
    return _Keys(required=_freeze_map(required), optional=_freeze_map(optional))


# ----- map_of ---------------------------------------------------------------

@dataclass(frozen=True)
class _MapOf(Spec):
    key_spec: Spec
    val_spec: Spec
    spec_name: str = ":persistence.spec/map-of"

    def _conform(self, value: Any) -> ConformResult:
        if not isinstance(value, dict):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected dict, got {type(value).__name__}",
                hint="provide a dict with uniform key/value types",
            )
        refined: dict[Any, Any] = {}
        errors: list[ConformError] = []
        for k, v in value.items():
            kr = self.key_spec.conform(k)
            vr = self.val_spec.conform(v)
            if not kr.is_ok:
                errors.append(kr.with_path(f"<key {k!r}>"))  # type: ignore[union-attr]
                continue
            if not vr.is_ok:
                errors.append(vr.with_path(k))  # type: ignore[union-attr]
                continue
            refined[kr.value] = vr.value  # type: ignore[union-attr]
        if errors:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"{len(errors)} entry/entries failed",
                sub_errors=tuple(errors),
            )
        return Conformed(value=refined, spec_key=self.spec_name)

    def _generate(self) -> dict[Any, Any]:
        n = _rng.randint(0, 4)
        return {self.key_spec.generate(): self.val_spec.generate() for _ in range(n)}


def map_of(key_spec: Spec, val_spec: Spec) -> Spec:
    return _MapOf(key_spec=key_spec, val_spec=val_spec)


# ----- seq_of ---------------------------------------------------------------

@dataclass(frozen=True)
class _SeqOf(Spec):
    element: Spec
    min_len: int = 0
    max_len: int | None = None
    spec_name: str = ":persistence.spec/seq-of"

    def _conform(self, value: Any) -> ConformResult:
        # strings are iterable but semantically scalar; reject outright
        if isinstance(value, (str, bytes, bytearray)):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected list/tuple, got {type(value).__name__}",
                hint="provide a list or tuple of elements",
            )
        if not isinstance(value, (list, tuple)):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected list/tuple, got {type(value).__name__}",
                hint="provide a list or tuple of elements",
            )
        if len(value) < self.min_len:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"length {len(value)} < min {self.min_len}",
                hint=f"provide at least {self.min_len} elements",
            )
        if self.max_len is not None and len(value) > self.max_len:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"length {len(value)} > max {self.max_len}",
                hint=f"provide at most {self.max_len} elements",
            )
        refined: list[Any] = []
        errors: list[ConformError] = []
        for i, v in enumerate(value):
            sub = self.element.conform(v)
            if not sub.is_ok:
                errors.append(sub.with_path(i))  # type: ignore[union-attr]
            else:
                refined.append(sub.value)  # type: ignore[union-attr]
        if errors:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"{len(errors)} element(s) failed",
                sub_errors=tuple(errors),
            )
        return Conformed(value=refined, spec_key=self.spec_name)

    def _generate(self) -> list[Any]:
        low = self.min_len
        high = self.max_len if self.max_len is not None else self.min_len + 3
        n = _rng.randint(low, max(low, high))
        return [self.element.generate() for _ in range(n)]


def seq_of(spec: Spec, *, min: int = 0, max: int | None = None) -> Spec:
    return _SeqOf(element=spec, min_len=min, max_len=max)


# ----- tuple_of (fixed, heterogeneous) --------------------------------------

@dataclass(frozen=True)
class _TupleOf(Spec):
    members: tuple[Spec, ...]
    spec_name: str = ":persistence.spec/tuple-of"

    def _conform(self, value: Any) -> ConformResult:
        if not isinstance(value, (list, tuple)):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected list/tuple, got {type(value).__name__}",
            )
        if len(value) != len(self.members):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected length {len(self.members)}, got {len(value)}",
                hint=f"provide exactly {len(self.members)} elements",
            )
        refined: list[Any] = []
        errors: list[ConformError] = []
        for i, (sp, v) in enumerate(zip(self.members, value)):
            sub = sp.conform(v)
            if not sub.is_ok:
                errors.append(sub.with_path(i))  # type: ignore[union-attr]
            else:
                refined.append(sub.value)  # type: ignore[union-attr]
        if errors:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"{len(errors)} element(s) failed",
                sub_errors=tuple(errors),
            )
        return Conformed(value=tuple(refined), spec_key=self.spec_name)

    def _generate(self) -> tuple[Any, ...]:
        return tuple(sp.generate() for sp in self.members)


def tuple_of(*specs: Spec) -> Spec:
    return _TupleOf(members=tuple(specs))


# ----- enum -----------------------------------------------------------------

@dataclass(frozen=True)
class _Enum(Spec):
    members: tuple[Any, ...]
    spec_name: str = ":persistence.spec/enum"

    def _conform(self, value: Any) -> ConformResult:
        if value in self.members:
            return Conformed(value=value, spec_key=self.spec_name)
        return ConformError(
            spec_key=self.spec_name,
            value=value,
            reason=f"not a member of {list(self.members)}",
            hint=f"provide one of: {list(self.members)}",
        )

    def _generate(self) -> Any:
        return _rng.choice(self.members) if self.members else None


def enum(*values: Any) -> Spec:
    return _Enum(members=tuple(values))


# ----- regex ----------------------------------------------------------------

def regex(pattern: str) -> Spec:
    return _SimpleRegex(pattern=pattern)


@dataclass(frozen=True)
class _SimpleRegex(Spec):
    """Regex spec with a best-effort pure-stdlib generator.

    We intentionally avoid an external ``rstr`` dependency; our generator
    handles the ascii-letter and digit character classes that appear in this
    module's canonical specs. For arbitrary regexes, generation may fail its
    own conform check (callers receive that as a test failure, as intended).
    """

    pattern: str
    spec_name: str = ":persistence.spec/regex"

    def _conform(self, value: Any) -> ConformResult:
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a string",
            )
        if re.fullmatch(self.pattern, value):
            return Conformed(value=value, spec_key=self.spec_name)
        return ConformError(
            spec_key=self.spec_name,
            value=value,
            reason=f"did not match /{self.pattern}/",
            hint=f"provide a string matching /{self.pattern}/",
        )

    def _generate(self) -> str:
        return _generate_for_regex(self.pattern)


def _generate_for_regex(pattern: str) -> str:
    """Tiny regex sampler covering the idioms the canonical specs use:

    ^[chars]{min,max}$     — bounded class
    ^[chars]+$             — 1+ of class
    ^\\d{n}$                — fixed digits
    ^[a-z]{n,m}$
    """
    core = pattern
    if core.startswith("^"):
        core = core[1:]
    if core.endswith("$"):
        core = core[:-1]

    # handle {n,m} / {n} / + / *
    m = re.fullmatch(r"(\[[^\]]+\]|\\d|\\w)(\{(\d+)(?:,(\d+))?\}|\+|\*)?", core)
    if m:
        cls, quant, lo, hi = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        chars = _class_chars(cls)
        if quant.startswith("{"):
            lo_n = int(lo)
            hi_n = int(hi) if hi else lo_n
            n = _rng.randint(lo_n, hi_n)
        elif quant == "+":
            n = _rng.randint(1, 5)
        elif quant == "*":
            n = _rng.randint(0, 5)
        else:
            n = 1
        return "".join(_rng.choices(chars, k=n))

    # literal pattern fallback: if the pattern is a literal string, return it
    if re.fullmatch(r"[\w\-:/. ]+", core):
        return core

    # last-resort: digits
    return "".join(_rng.choices(string.digits, k=3))


def _class_chars(cls: str) -> str:
    if cls == r"\d":
        return string.digits
    if cls == r"\w":
        return string.ascii_letters + string.digits + "_"
    # bracketed class: [a-z], [0-9], [a-zA-Z0-9_], etc.
    body = cls[1:-1]
    out = []
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            lo, hi = body[i], body[i + 2]
            out.extend(chr(c) for c in range(ord(lo), ord(hi) + 1))
            i += 3
        else:
            out.append(body[i])
            i += 1
    return "".join(out) or string.ascii_lowercase


# ----- ref ------------------------------------------------------------------

@dataclass(frozen=True)
class _Ref(Spec):
    key: str
    spec_name: str = ":persistence.spec/ref"

    def _conform(self, value: Any) -> ConformResult:
        from ._registry import _REGISTRY  # avoid circular
        resolved = _REGISTRY.get(self.key)
        if resolved is None:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"ref points to unknown spec key {self.key!r}",
                hint=f"register a spec under {self.key!r} via spec.register()",
            )
        return resolved.conform(value)

    def _generate(self) -> Any:
        from ._registry import _REGISTRY
        resolved = _REGISTRY.get(self.key)
        if resolved is None:
            return None
        return resolved.generate()


def ref(key: str) -> Spec:
    return _Ref(key=key)
