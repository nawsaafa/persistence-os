"""EDN parse / unparse for persistence.plan ASTs."""
from __future__ import annotations

from typing import Any, Mapping

import edn_format

from persistence.plan._ast import Node
from persistence.plan._errors import ParseError


def _edn_key_to_str(key: Any) -> str:
    """Convert an EDN map key to a plain Python string attr name.

    Keyword keys like :prompt → "prompt" (strip the leading colon).
    String keys pass through unchanged.
    """
    if isinstance(key, edn_format.Keyword):
        return key.name  # "namespace/local" or "local" — no leading colon for attr names
    return str(key)


def _edn_to_python(obj: Any) -> Any:
    """Recursively convert edn_format objects to plain Python values.

    - edn_format.Keyword → ":name" string form (name already includes namespace/local)
    - edn_format.ImmutableDict → dict with keyword keys converted to plain strings
    - edn_format.ImmutableList → list of converted values
    - list/tuple → list of converted values (defensive)
    - scalars → unchanged
    """
    if isinstance(obj, edn_format.Keyword):
        # obj.name already holds "namespace/localname" for namespaced keywords
        return f":{obj.name}"
    if isinstance(obj, edn_format.ImmutableDict):
        # Keyword keys become plain string attr names (strip leading colon).
        # Keyword values stay as ":name" strings.
        return {_edn_key_to_str(k): _edn_to_python(v) for k, v in obj.items()}
    if isinstance(obj, edn_format.ImmutableList):
        return [_edn_to_python(x) for x in obj]
    if isinstance(obj, (list, tuple)):
        return [_edn_to_python(x) for x in obj]
    # Scalars (str, int, bool, None, float)
    return obj


def _python_to_node(obj: Any) -> Node:
    """Convert edn_format-parsed Python value to Node tree.

    Expected shape: [tag, attrs_dict, *children] where tag is ":keyword" string,
    attrs_dict is a dict (may be empty), children are recursive node shapes.
    """
    if not isinstance(obj, list):
        raise ParseError(f"expected EDN vector for node, got {type(obj).__name__}: {obj!r}")
    if len(obj) < 2:
        raise ParseError(f"node vector too short (need tag + attrs): {obj!r}")

    tag = obj[0]
    if not isinstance(tag, str) or not tag.startswith(":"):
        raise ParseError(f"node tag must be keyword, got {tag!r}")

    attrs_raw = obj[1]
    if not isinstance(attrs_raw, dict):
        raise ParseError(f"node attrs must be map, got {type(attrs_raw).__name__}: {attrs_raw!r}")

    children_raw = obj[2:]
    children = tuple(_python_to_node(c) for c in children_raw)

    return Node(tag=tag, attrs=attrs_raw, children=children)


def parse(
    edn_text: str,
    *,
    lower_aliases: Mapping[str, str] | None = None,
    strict: bool = True,
) -> Node:
    """Parse EDN text to Node. Validates against :persistence.plan/node.

    Args:
        edn_text: EDN source text (single top-level vector).
        lower_aliases: optional {":alias": ":target"} to lower alias tags at read time.
            Example: {":phase": ":seq", ":workstream": ":seq"} for reading track plan.edn.
        strict: if True (default), validate against :persistence.plan/node spec and
            raise ConformError on failure. Set False to skip validation (testing only).

    Raises:
        ParseError: malformed EDN or wrong shape.
        ConformError: AST fails spec validation (strict=True only).
    """
    try:
        raw = edn_format.loads(edn_text)
    except Exception as exc:
        raise ParseError(f"EDN tokenize failed: {exc}") from exc

    py = _edn_to_python(raw)

    try:
        node = _python_to_node(py)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"shape conversion failed: {exc}") from exc

    if lower_aliases:
        node = _apply_aliases(node, lower_aliases)

    if strict:
        _validate_spec(node)

    return node


def _apply_aliases(node: Node, aliases: Mapping[str, str]) -> Node:
    """Recursively lower alias tags. Alias children lowered too."""
    new_tag = aliases.get(node.tag, node.tag)
    new_children = tuple(_apply_aliases(c, aliases) for c in node.children)
    return Node(tag=new_tag, attrs=dict(node.attrs), children=new_children)


def _validate_spec(node: Node) -> None:
    """Stub — real implementation in Task 13."""
    return


def unparse(node: Node) -> str:
    """Placeholder — real implementation in Task 15."""
    raise NotImplementedError
