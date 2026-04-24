# tests/plan/test_property.py
"""Hypothesis property tests for claims 1 (content-addressing) + 2 (round-trip).

R2 M4. Three cherry-picked tests in test_ast.py pin the invariants on
specific Node shapes. Hypothesis generates hundreds of random Nodes per
run and exercises the same invariants — a real content-addressing claim
must survive arbitrary structures, not just the ones the authors imagined.

Strategies:
- plan_attr_value_strat  — JSON-safe recursive values (str, int, bool, None,
  finite floats, nested dicts with plain-string keys). Bounded depth + size.
- plan_node_strat — recursive Node strategy: random tag from the 16 kinds,
  random attrs, random children.

Properties:
1. Round-trip preserves :id — parse(unparse(n), strict=False).id == n.id
2. Unparse is idempotent after round-trip — unparse(parse(unparse(n))) == unparse(n)
3. Attr-key insertion order doesn't affect :id
4. Distinct structure (changed attr / tag / children) yields distinct :id
5. Descendant mutation propagates to every ancestor id
"""
from __future__ import annotations

from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, HealthCheck

from persistence.plan import Node, parse, unparse


# All 16 plan node kinds registered in the :persistence.plan/node enum.
PLAN_KINDS = (
    ":seq", ":par", ":choice", ":loop", ":race", ":let", ":branch", ":case",
    ":tool-call", ":llm-call", ":code", ":checkpoint",
    ":reflect", ":verify", ":call-skill", ":ref",
)

# Kinds that commonly carry children (used to bias child generation — any
# leaf can also have children in v0.1, but these are the natural containers).
_CONTAINER_KINDS = (":seq", ":par", ":choice", ":loop", ":race", ":let", ":branch", ":case")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Plain-string attr keys — EDN keyword-legal alphabet only.
# EDN keywords per the spec accept [A-Za-z0-9+_?!*-./] but edn_format is
# stricter about leading chars; we keep it simple by restricting to letters,
# digits, '-', '_' starting with a letter. This is a subset of what unparse
# can emit safely. Broader EDN keyword coverage is out of scope for the
# content-addressing properties — any legal key shape exercises the same
# canonical-form logic.
_attr_key_strat = st.from_regex(r"[a-z][a-z0-9_-]{0,7}", fullmatch=True)


def _scalar_strat() -> st.SearchStrategy[Any]:
    """JSON-safe scalars only; finite floats (no NaN/Inf per R2 C1)."""
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**9), max_value=10**9),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        # Strings: avoid EDN-metasyntax chars and the leading-colon case which
        # would be emitted bare (valid EDN keyword, but not what we want for
        # an arbitrary attr value).
        st.text(
            alphabet=st.characters(
                min_codepoint=0x20, max_codepoint=0x7e,
                blacklist_characters='":\\',
            ),
            min_size=0,
            max_size=20,
        ),
    )


@st.composite
def plan_attr_value_strat(draw, depth: int = 2) -> Any:
    """Recursive attr-value strategy: scalars at depth 0, nested dicts
    of scalars at depth>0. Bounded at depth=2 by default to keep search
    fast and canonical forms readable when hypothesis reports a failure.
    """
    if depth <= 0:
        return draw(_scalar_strat())
    return draw(st.one_of(
        _scalar_strat(),
        st.dictionaries(
            keys=_attr_key_strat,
            values=plan_attr_value_strat(depth=depth - 1),
            max_size=4,
        ),
    ))


def _attrs_dict_strat() -> st.SearchStrategy[dict]:
    return st.dictionaries(
        keys=_attr_key_strat,
        values=plan_attr_value_strat(depth=2),
        max_size=4,
    )


@st.composite
def plan_node_strat(draw, max_depth: int = 3) -> Node:
    """Recursive Node strategy bounded at max_depth."""
    tag = draw(st.sampled_from(PLAN_KINDS))
    attrs = draw(_attrs_dict_strat())
    if max_depth <= 0:
        children: tuple[Node, ...] = ()
    else:
        children = tuple(draw(
            st.lists(
                plan_node_strat(max_depth=max_depth - 1),
                max_size=3,
            )
        ))
    return Node(tag=tag, attrs=attrs, children=children)


# ---------------------------------------------------------------------------
# Property 1: round-trip preserves :id
# ---------------------------------------------------------------------------

@given(node=plan_node_strat())
@settings(max_examples=50, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
def test_round_trip_preserves_id(node: Node):
    """Claim 2 — parse(unparse(n), strict=False).id == n.id for any Node.

    The parser auto-strips any :id that leaks through _edn_to_python (R2 C2),
    so the round-tripped Node rebuilds :id from canonical form. If unparse
    drops or reorders any semantic content, this property fails.
    """
    emitted = unparse(node)
    re_parsed = parse(emitted, strict=False)
    assert re_parsed.id == node.id, (
        f"round-trip broke :id\nnode={node}\nemitted={emitted}\n"
        f"re_parsed={re_parsed}\noriginal.id={node.id}\nround-trip.id={re_parsed.id}"
    )


# ---------------------------------------------------------------------------
# Property 2: unparse is idempotent after round-trip
# ---------------------------------------------------------------------------

@given(node=plan_node_strat())
@settings(max_examples=50, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
def test_unparse_parse_idempotent(node: Node):
    """unparse(parse(unparse(n))) == unparse(n) — canonicalisation is stable.

    unparse emits the canonical form; parsing it and re-emitting must be a
    no-op. Any non-determinism in attr-key ordering or value emission would
    show up here.
    """
    once = unparse(node)
    twice = unparse(parse(once, strict=False))
    assert once == twice, f"unparse not idempotent\nonce={once}\ntwice={twice}"


# ---------------------------------------------------------------------------
# Property 3: attr-key insertion order invariance
# ---------------------------------------------------------------------------

@given(
    tag=st.sampled_from(PLAN_KINDS),
    attrs=st.lists(
        st.tuples(_attr_key_strat, _scalar_strat()),
        min_size=2, max_size=5,
        unique_by=lambda kv: kv[0],  # unique keys
    ),
)
@settings(max_examples=50, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
def test_attr_key_order_invariance(tag: str, attrs: list):
    """Two Nodes built from the same {k: v} pairs in different orders hash
    identically. Canonical form sorts keys at JSON emission; this property
    is what makes dict-literal Node construction safe."""
    attrs_forward = dict(attrs)
    attrs_reversed = dict(reversed(attrs))

    a = Node(tag=tag, attrs=attrs_forward, children=())
    b = Node(tag=tag, attrs=attrs_reversed, children=())
    assert a.id == b.id


# ---------------------------------------------------------------------------
# Property 4: distinct structure → distinct :id
# ---------------------------------------------------------------------------

@given(node=plan_node_strat(), sentinel_key=_attr_key_strat, sentinel_val=st.text(min_size=1, max_size=8, alphabet=st.characters(min_codepoint=0x41, max_codepoint=0x7a)))
@settings(max_examples=50, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
def test_distinct_attrs_yields_distinct_id(node: Node, sentinel_key: str, sentinel_val: str):
    """Adding an attr key (that wasn't there) changes :id with high probability.

    We add a key not present in the original attrs — strictly changes the
    canonical form, strictly changes :id. 128-bit truncation makes collision
    probability ~2^-128, negligible.
    """
    if sentinel_key in node.attrs:
        # Degenerate: sentinel already present; skip this iteration.
        return
    new_attrs = dict(node.attrs)
    new_attrs[sentinel_key] = sentinel_val
    mutated = Node(tag=node.tag, attrs=new_attrs, children=node.children)
    assert mutated.id != node.id


@given(node=plan_node_strat(), new_tag=st.sampled_from(PLAN_KINDS))
@settings(max_examples=50, deadline=1000, suppress_health_check=[HealthCheck.too_slow])
def test_distinct_tag_yields_distinct_id(node: Node, new_tag: str):
    """Changing tag to a different tag changes :id."""
    if new_tag == node.tag:
        return
    mutated = Node(tag=new_tag, attrs=dict(node.attrs), children=node.children)
    assert mutated.id != node.id


# ---------------------------------------------------------------------------
# Property 5: descendant mutation propagates to every ancestor
# ---------------------------------------------------------------------------

def _all_ids(node: Node) -> list[str]:
    """Collect :id of node + all descendants (pre-order)."""
    out = [node.id]
    for c in node.children:
        out.extend(_all_ids(c))
    return out


def _mutate_deepest(node: Node, sentinel: str) -> Node:
    """Rebuild the tree with the deepest-left leaf's attrs mutated. Returns
    (new_root, path_of_changed_node_ids_from_old_tree)."""
    if not node.children:
        # Leaf — mutate attrs.
        new_attrs = dict(node.attrs)
        new_attrs["_test_sentinel"] = sentinel
        return Node(tag=node.tag, attrs=new_attrs, children=())
    new_children = list(node.children)
    new_children[0] = _mutate_deepest(new_children[0], sentinel)
    return Node(tag=node.tag, attrs=dict(node.attrs), children=tuple(new_children))


def _ancestor_chain_ids(node: Node) -> list[str]:
    """Walk leftmost descent, collecting every id from root down to the leaf."""
    ids = [node.id]
    cur = node
    while cur.children:
        cur = cur.children[0]
        ids.append(cur.id)
    return ids


@given(node=plan_node_strat(max_depth=4), sentinel=st.text(min_size=3, max_size=6, alphabet="abcdef"))
@settings(max_examples=40, deadline=1500, suppress_health_check=[HealthCheck.too_slow])
def test_descendant_mutation_propagates(node: Node, sentinel: str):
    """Mutating the deepest-left leaf changes the id of EVERY node on the
    path from root to that leaf — the Merkle-DAG recursion invariant.

    "_test_sentinel" is rare enough (not in the strategy) that the mutation
    strictly changes the leaf; if any ancestor id stays the same the recursion
    is broken.
    """
    mutated = _mutate_deepest(node, sentinel)

    before = _ancestor_chain_ids(node)
    after = _ancestor_chain_ids(mutated)

    # Chain length unchanged (we only edited attrs at the leaf, not shape).
    assert len(before) == len(after)

    # EVERY id along the chain must have changed.
    for i, (b, a) in enumerate(zip(before, after)):
        assert b != a, (
            f"ancestor at depth {i} failed to propagate change\n"
            f"before={before}\nafter={after}"
        )
