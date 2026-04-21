"""Persistence OS — Module 6: Spec.

The boundary-contract layer. Specs are predicate-generator pairs; conforming a
value returns either a :class:`Conformed` (refined, typed) or a
:class:`ConformError` (structured failure, self-healing-ready).

Public API:

- Core types: :class:`Spec`, :class:`Conformed`, :class:`ConformError`, :data:`ConformResult`
- Primitives: :func:`int_`, :func:`float_`, :func:`bool_`, :func:`str_`,
  :func:`bytes_`, :func:`uuid_`, :func:`inst`
- Combinators: :func:`and_`, :func:`or_`, :func:`not_`, :func:`maybe`,
  :func:`keys`, :func:`map_of`, :func:`seq_of`, :func:`tuple_of`, :func:`enum`,
  :func:`regex`, :func:`ref`
- Registry: :func:`register`, :func:`get`, :func:`conform`, :func:`parse`,
  :func:`generate_example`, :func:`explain_for_llm`, :func:`quickcheck`

See ``paper/persistence-nesy-2026-draft.md`` §§4.6, 5.6 for the formal surface.
"""
from __future__ import annotations

from ._combinators import (
    and_,
    enum,
    keys,
    map_of,
    maybe,
    not_,
    or_,
    ref,
    regex,
    seq_of,
    tuple_of,
)
from ._primitives import (
    bool_,
    bytes_,
    float_,
    inst,
    int_,
    str_,
    uuid_,
)
from ._registry import (
    conform,
    explain_for_llm,
    generate_example,
    get,
    parse,
    quickcheck,
    register,
    registered_keys,
)
from ._types import ConformError, Conformed, ConformResult, Spec

# canonical spec registration happens on import so users never have to call it
from . import _canonical  # noqa: F401

__all__ = [
    # types
    "Spec",
    "Conformed",
    "ConformError",
    "ConformResult",
    # primitives
    "int_",
    "float_",
    "bool_",
    "str_",
    "bytes_",
    "uuid_",
    "inst",
    # combinators
    "and_",
    "or_",
    "not_",
    "maybe",
    "keys",
    "map_of",
    "seq_of",
    "tuple_of",
    "enum",
    "regex",
    "ref",
    # registry
    "register",
    "get",
    "registered_keys",
    "conform",
    "parse",
    "generate_example",
    "explain_for_llm",
    "quickcheck",
]
