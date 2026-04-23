"""EDN parse / unparse for persistence.plan ASTs."""
from __future__ import annotations

from typing import Any, Mapping

import edn_format

from persistence.plan._ast import Node
from persistence.plan._errors import ParseError


class PlanSpecError(Exception):
    """Raised when a parsed Node fails :persistence.plan/node spec validation.

    Wraps the structured ConformError for programmatic inspection:

        try:
            node = parse(edn, strict=True)
        except PlanSpecError as exc:
            print(exc.conform_error.spec_key, exc.conform_error.reason)

    The ConformError is also available as ``exc.args[0]`` for generic inspection.
    The ``spec_key`` property mirrors ``conform_error.spec_key`` for quick access.
    """

    def __init__(self, conform_error: Any) -> None:
        self.conform_error = conform_error
        super().__init__(conform_error)

    @property
    def spec_key(self) -> Any:
        return self.conform_error.spec_key


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
        PlanSpecError: AST fails spec validation (strict=True only).
            Wraps a structured ConformError; access via ``exc.conform_error``.
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
    """Validate node against :persistence.plan/node registered spec.

    Converts Node (internal representation: plain-string attr keys,
    computed Node.id) to the external vector form the spec validates:
    ``[tag, {:id "sha256:<hex>", <keyword-keyed attrs>}, *child-vectors]``.

    Node.id is content-addressed (sha256 of canonical form) — we prefix
    with ``sha256:`` to match :persistence.spec/sha256's format contract.
    Internal Node.attrs is unchanged; the injected :id is ephemeral and
    lives only in the vector passed to conform().

    Raises PlanSpecError (wrapping ConformError) on validation failure;
    the error carries .conform_error.spec_key, .conform_error.path,
    .conform_error.hint for callers to inspect.
    """
    from persistence.spec import conform

    vector = _to_vector_form(node)
    result = conform(":persistence.plan/node", vector)
    if not result.is_ok:
        raise PlanSpecError(result)


def _to_vector_form(node: Node) -> list:
    """Build the external vector representation the spec validates.

    Internal Node uses plain-string attr keys like "prompt"; the spec
    demands keyword-form keys like ":prompt". We prepend ':' at emit time.
    Injects :id derived from Node.id (16 hex chars → "sha256:<hex>").
    """
    keyword_attrs: dict[str, Any] = {":id": f"sha256:{node.id}"}
    for k, v in node.attrs.items():
        # Attr keys in internal Node are plain strings (no leading ':');
        # spec requires keyword-form keys — prepend ':' unless already present.
        key = k if k.startswith(":") else f":{k}"
        keyword_attrs[key] = v
    return [node.tag, keyword_attrs, *(_to_vector_form(c) for c in node.children)]


def unparse(node: Node) -> str:
    """Emit canonical EDN for node. Round-trip invariant:
    ``unparse(parse(x, strict=False)) == x`` for canonical inputs.

    Canonical form:
    - Node: ``[<tag> <attrs> <child1> <child2> ...]`` space-separated
    - Attrs: ``{<:k1> <v1> <:k2> <v2> ...}`` keys sorted alphabetically,
      attr-name keys emitted as keywords (leading colon added back)
    - Strings: double-quoted with backslash escaping of ``"`` and ``\\``
    - Keyword-form strings (start with ``:``): emitted bare
    - Integers: base-10
    - Floats: ``repr()`` (deterministic)
    - Booleans: ``true`` / ``false``
    - Nil: ``nil``
    - Nested maps inside attr values: treated as attr-maps (symmetric with parse
      which stripped colons at every map level)
    """
    return _emit_node(node)


def _emit_node(node: Node) -> str:
    """Emit a Node as canonical EDN vector."""
    parts = [node.tag, _emit_attrs(dict(node.attrs))]
    for child in node.children:
        parts.append(_emit_node(child))
    return "[" + " ".join(parts) + "]"


def _emit_attrs(attrs: dict) -> str:
    """Emit an attr map. Keys are attr names (plain strings internally) —
    re-prefix with ':' to restore keyword form. Values go through _emit_value.
    Keys sorted alphabetically for canonical ordering.
    """
    if not attrs:
        return "{}"
    items = sorted(attrs.items(), key=lambda kv: kv[0])
    parts = []
    for k, v in items:
        key_edn = k if k.startswith(":") else f":{k}"
        parts.append(f"{key_edn} {_emit_value(v)}")
    return "{" + " ".join(parts) + "}"


def _emit_value(v: Any) -> str:
    """Emit a non-attr-key value as canonical EDN."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        if v.startswith(":"):
            # Keyword form — emit bare
            return v
        # String — double-quote with backslash escaping
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, dict):
        # A dict inside an attr value is still an attr-shaped map
        # (parse stripped colons at every map level; unparse restores them).
        return _emit_attrs(v)
    if isinstance(v, (list, tuple)):
        return "[" + " ".join(_emit_value(x) for x in v) + "]"
    raise TypeError(f"unparse: cannot emit value of type {type(v).__name__}: {v!r}")
