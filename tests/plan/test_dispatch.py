"""Phase A — Dispatcher: handler-per-tag registry over the existing walker."""
from __future__ import annotations

import pytest


def test_dispatcher_register_stores_handler():
    """Dispatcher.register(tag, handler) stores the handler keyed by tag."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    handler = lambda node, env: env  # no-op
    d.register(":fact", handler)
    assert d.has_handler(":fact")
    assert not d.has_handler(":no-such-tag")


def test_dispatcher_register_rejects_duplicate_without_replace():
    """Re-registering the same tag without replace=True raises ValueError."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    d.register(":fact", lambda node, env: env)
    with pytest.raises(ValueError) as excinfo:
        d.register(":fact", lambda node, env: env)
    assert ":fact" in str(excinfo.value)


def test_dispatcher_register_replace_overrides():
    """Re-registering with replace=True overwrites the prior handler."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    d.register(":fact", lambda node, env: "first")
    d.register(":fact", lambda node, env: "second", replace=True)
    # Verified indirectly via dispatch (later test), or via has_handler
    assert d.has_handler(":fact")


def test_dispatcher_dispatch_calls_handlers_in_walk_order():
    """Dispatch order matches the existing walker's DFS order."""
    from persistence.plan import Dispatcher, Node

    # Build a small tree:  :seq [ :a, :b [ :c ] ]
    tree = Node(
        tag=":seq",
        children=(
            Node(tag=":a"),
            Node(tag=":b", children=(Node(tag=":c"),)),
        ),
    )

    d = Dispatcher()
    seen: list[str] = []
    d.register(":seq", lambda n, env: seen.append(":seq") or "seq")
    d.register(":a", lambda n, env: seen.append(":a") or "a")
    d.register(":b", lambda n, env: seen.append(":b") or "b")
    d.register(":c", lambda n, env: seen.append(":c") or "c")

    results = d.dispatch(tree, env={})

    # DFS pre-order: :seq, :a, :b, :c
    assert seen == [":seq", ":a", ":b", ":c"]
    assert results == ["seq", "a", "b", "c"]


def test_dispatcher_skips_unregistered_tags_silently():
    """Nodes whose tag has no registered handler are skipped (no error)."""
    from persistence.plan import Dispatcher, Node

    tree = Node(
        tag=":seq",
        children=(Node(tag=":a"), Node(tag=":b")),
    )
    d = Dispatcher()
    d.register(":a", lambda n, env: "a-result")  # only :a registered
    results = d.dispatch(tree, env={})
    assert results == ["a-result"]


def test_dispatcher_passes_env_by_reference():
    """The env dict is passed by reference (same object id to every handler)."""
    from persistence.plan import Dispatcher, Node

    tree = Node(tag=":x", children=(Node(tag=":x"),))
    seen_envs: list[int] = []
    d = Dispatcher()
    d.register(":x", lambda n, env: seen_envs.append(id(env)))
    env = {"key": "value"}
    d.dispatch(tree, env=env)
    assert len(seen_envs) == 2
    assert seen_envs[0] == seen_envs[1] == id(env)


def test_dispatcher_env_mutation_propagates_to_later_handlers():
    """Handler mutations to env are visible to subsequent handlers in the same walk.

    Pins the documented design guarantee in Dispatcher.dispatch docstring:
    env is the implicit shared-state thread between handlers.
    """
    from persistence.plan import Dispatcher, Node

    tree = Node(tag=":seq", children=(Node(tag=":a"), Node(tag=":b")))
    d = Dispatcher()
    d.register(":seq", lambda n, env: env.setdefault("touched_by", []).append(":seq"))
    d.register(":a", lambda n, env: env["touched_by"].append(":a"))
    d.register(":b", lambda n, env: env["touched_by"].append(":b"))
    env: dict = {}
    d.dispatch(tree, env=env)
    assert env["touched_by"] == [":seq", ":a", ":b"]


def test_dispatcher_replace_handler_takes_effect():
    """Re-registering with replace=True changes which handler is called."""
    from persistence.plan import Dispatcher, Node

    d = Dispatcher()
    d.register(":x", lambda n, env: "first")
    d.register(":x", lambda n, env: "second", replace=True)
    results = d.dispatch(Node(tag=":x"), env={})
    assert results == ["second"]


def test_dispatcher_propagates_unimplemented_kind_from_walker():
    """:code and :branch leaves still raise UnimplementedNodeKindError —
    the dispatcher does not paper over the walker's contract."""
    from persistence.plan import Dispatcher, Node, UnimplementedNodeKindError

    tree = Node(tag=":code")  # no children → leaf → walker raises
    d = Dispatcher()
    with pytest.raises(UnimplementedNodeKindError):
        d.dispatch(tree, env={})


from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402
from persistence.plan import Dispatcher, Node, walk  # noqa: E402

# Keep tag space small to ensure handler coverage across all property runs.
_DISPATCH_TAG_ST = st.sampled_from([":a", ":b", ":c", ":seq"])


@st.composite
def _small_tree(draw, depth=0):
    tag = draw(_DISPATCH_TAG_ST)
    if depth >= 3 or draw(st.booleans()):
        return Node(tag=tag)
    n_children = draw(st.integers(min_value=0, max_value=3))
    children = tuple(draw(_small_tree(depth=depth + 1)) for _ in range(n_children))
    return Node(tag=tag, children=children)


@given(_small_tree())
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_dispatcher_property_dispatch_order_equals_walk_order(tree):
    """For arbitrary (small) trees, dispatch order matches walk order.

    Property: if all node tags have a registered echo handler, the
    list of (id, tag) pairs from dispatch matches the walker's trace.
    """
    d = Dispatcher()
    for tag in (":a", ":b", ":c", ":seq"):
        d.register(tag, lambda n, env: (n.id, n.tag), replace=True)
    results = d.dispatch(tree, env={})
    # Compare against walker trace
    trace = walk(tree)
    assert [r[0] for r in results] == trace
