"""EDN chain schema parser + serializer for persistence-orchestrate.

LD-3 (codex consensus REJECT-FOR-NEW-OPTION → EDN canonical):
Chain schemas are authored in EDN. v0 is EDN-only authoring; YAML is
deferred to W3-5. SKILL.md frontmatter stays YAML (Anthropic
convention, non-load-bearing).

R0-fold B2: YAML-shaped input must raise ChainSchemaError with the
verbatim "v0 is EDN-only; YAML authoring is W3-5" message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import edn_format


class ChainSchemaError(ValueError):
    """Raised when chain.edn fails schema validation."""


@dataclass(frozen=True)
class StepCapability:
    """Per-step capability requirement (Capability lattice from 2.3d)."""
    op: str
    qualifier: str


@dataclass(frozen=True)
class Step:
    """A single chain step."""
    id: int
    op: str  # EDN keyword form, e.g. ":fs/read"
    args: dict[str, Any] = field(default_factory=dict)
    capability: StepCapability | None = None


@dataclass(frozen=True)
class Chain:
    """A complete chain ready for emission."""
    name: str
    description: str
    steps: tuple[Step, ...]


def _is_yaml_shaped(src: str) -> bool:
    """Heuristic: looks like YAML (top-level `key: value` lines, no parens)."""
    stripped = src.strip()
    if stripped.startswith("("):
        return False
    # YAML signature: at least one `key: value` line at column 0
    for line in stripped.splitlines()[:5]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            if ":" in line and not line.startswith("("):
                return True
    return False


def _normalize_tagged_list(parsed: Any, expected_tag: str) -> dict[Any, Any]:
    """Normalize a tagged-list EDN form `(:tag :key1 v1 :key2 v2 ...)` into a dict.

    The canonical chain.edn form uses list syntax with a leading tag
    keyword (e.g. `(:chain :name "demo" ...)`). `edn_format.loads`
    returns this as a tuple of alternating keyword/value pairs with the
    tag keyword first. This helper strips the tag and zips the remaining
    pairs into a dict.

    If `parsed` is already a dict (brace-form), pass through unchanged.
    """
    if isinstance(parsed, dict):
        return dict(parsed)
    if not isinstance(parsed, (list, tuple, edn_format.ImmutableList)):
        raise ChainSchemaError(
            f"expected tagged-list or map for {expected_tag}; got "
            f"{type(parsed).__name__}"
        )
    items = list(parsed)
    if not items:
        raise ChainSchemaError(f"empty tagged-list for {expected_tag}")
    head = items[0]
    expected_name = expected_tag.lstrip(":")
    if not isinstance(head, edn_format.Keyword) or str(head).lstrip(":") != expected_name:
        raise ChainSchemaError(
            f"tagged-list must start with {expected_tag}; got {head!r}"
        )
    rest = items[1:]
    if len(rest) % 2 != 0:
        raise ChainSchemaError(
            f"{expected_tag} tagged-list has odd number of key/value items"
        )
    out: dict[Any, Any] = {}
    for i in range(0, len(rest), 2):
        out[rest[i]] = rest[i + 1]
    return out


def parse_chain_edn(src: str) -> Chain:
    """Parse an EDN chain source string into a Chain dataclass.

    Raises ChainSchemaError on missing required fields or YAML input.
    """
    if _is_yaml_shaped(src):
        raise ChainSchemaError(
            "v0 is EDN-only; YAML authoring is W3-5 (deferred)"
        )

    try:
        parsed_raw = edn_format.loads(src)
    except Exception as e:
        raise ChainSchemaError(f"EDN parse error: {e}") from e

    try:
        parsed = _normalize_tagged_list(parsed_raw, ":chain")
    except ChainSchemaError:
        raise
    except Exception as e:
        raise ChainSchemaError(
            f"chain.edn must be a (:chain ...) tagged-list; got "
            f"{type(parsed_raw).__name__}"
        ) from e

    # Extract fields by EDN keyword
    name_kw = edn_format.Keyword("name")
    desc_kw = edn_format.Keyword("description")
    steps_kw = edn_format.Keyword("steps")

    if name_kw not in parsed:
        raise ChainSchemaError("missing required field: :name")
    if desc_kw not in parsed:
        raise ChainSchemaError("missing required field: :description")
    if steps_kw not in parsed:
        raise ChainSchemaError("missing required field: :steps")

    name = parsed[name_kw]
    description = parsed[desc_kw]
    raw_steps = parsed[steps_kw]

    if not isinstance(name, str):
        raise ChainSchemaError(":name must be a string")
    if not isinstance(description, str):
        raise ChainSchemaError(":description must be a string")
    if not isinstance(raw_steps, (list, tuple, edn_format.ImmutableList)):
        raise ChainSchemaError(":steps must be a vector")

    steps = tuple(_parse_step(s) for s in raw_steps)
    return Chain(name=name, description=description, steps=steps)


def _parse_step(raw: Any) -> Step:
    try:
        src = _normalize_tagged_list(raw, ":step")
    except ChainSchemaError:
        raise
    except Exception as e:
        raise ChainSchemaError(
            f"each step must be a (:step ...) tagged-list; got "
            f"{type(raw).__name__}"
        ) from e

    id_kw = edn_format.Keyword("id")
    op_kw = edn_format.Keyword("op")
    args_kw = edn_format.Keyword("args")
    cap_kw = edn_format.Keyword("capability")

    if id_kw not in src:
        raise ChainSchemaError("step missing required field: :id")
    if op_kw not in src:
        raise ChainSchemaError("step missing required field: :op")

    step_id = src[id_kw]
    op_raw = src[op_kw]

    if not isinstance(step_id, int):
        raise ChainSchemaError(":id must be an integer")

    # Normalize EDN keyword to string form ":fs/read"
    if isinstance(op_raw, edn_format.Keyword):
        kw_str = str(op_raw)
        op = kw_str if kw_str.startswith(":") else ":" + kw_str
    elif isinstance(op_raw, str):
        op = op_raw if op_raw.startswith(":") else ":" + op_raw
    else:
        raise ChainSchemaError(f":op must be a keyword or string; got {type(op_raw).__name__}")

    # Normalize args (dict with keyword keys → dict with string keys ":path")
    args: dict[str, Any] = {}
    if args_kw in src:
        for k, v in src[args_kw].items():
            if isinstance(k, edn_format.Keyword):
                k_str = str(k)
                key_str = k_str if k_str.startswith(":") else ":" + k_str
            else:
                key_str = str(k)
            args[key_str] = v

    capability: StepCapability | None = None
    if cap_kw in src:
        try:
            cap_raw = _normalize_tagged_list(src[cap_kw], ":Capability")
        except ChainSchemaError:
            raise
        except Exception as e:
            raise ChainSchemaError(":capability must be a (:Capability ...) tagged-list") from e
        cap_op = cap_raw.get(edn_format.Keyword("op"))
        cap_qual = cap_raw.get(edn_format.Keyword("qualifier"))
        if not isinstance(cap_op, str) or not isinstance(cap_qual, str):
            raise ChainSchemaError(":capability requires :op and :qualifier strings")
        capability = StepCapability(op=cap_op, qualifier=cap_qual)

    return Step(id=step_id, op=op, args=args, capability=capability)


def serialize_chain_edn(chain: Chain) -> str:
    """Serialize a Chain back to canonical EDN form.

    Deterministic: same Chain → byte-identical output. No wall-clock,
    no random, no dict-ordering nondeterminism.
    """
    lines = [
        "(:chain",
        f'  :name "{_escape(chain.name)}"',
        f'  :description "{_escape(chain.description)}"',
        "  :steps [",
    ]
    for step in chain.steps:
        lines.append("    " + _serialize_step(step))
    lines.append("  ])")
    return "\n".join(lines)


def _serialize_step(step: Step) -> str:
    parts = [f"(:step :id {step.id}", f":op {step.op}"]

    if step.args:
        args_parts = []
        for k in sorted(step.args.keys()):  # sorted for determinism
            v = step.args[k]
            if isinstance(v, str):
                args_parts.append(f'{k} "{_escape(v)}"')
            else:
                args_parts.append(f"{k} {v}")
        parts.append("  :args {" + " ".join(args_parts) + "}")

    if step.capability is not None:
        parts.append(
            f'  :capability (:Capability :op "{step.capability.op}" '
            f':qualifier "{step.capability.qualifier}")'
        )

    return " ".join(parts) + ")"


def _escape(s: str) -> str:
    """Minimal EDN string escape."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
