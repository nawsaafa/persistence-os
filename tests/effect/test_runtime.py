"""Runtime tests — Effect primitive, Handler, stack dispatch, mask, named handlers.

Models paper §4.2: a stack dispatches op κ to the outermost handler whose
domain contains κ; that handler invokes the continuation k which delegates
to the remaining stack. Proposition 2 (well-formedness) is enforced here.
"""
import pytest

from persistence.effect.runtime import (
    Effect,
    Handler,
    Runtime,
    Unhandled,
    mask,
    named,
    perform,
    with_runtime,
)


# ---------- basic dispatch ----------


def test_unhandled_effect_raises_when_no_runtime():
    """Performing without an active runtime raises Unhandled."""
    with pytest.raises(Unhandled):
        perform(":llm/call", model="x")


def test_single_handler_receives_op_and_args():
    seen: list = []

    def clause(args, k, ctx):
        seen.append((args, ctx))
        return {"text": "ok"}

    rt = Runtime([Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": clause})])
    with with_runtime(rt):
        out = perform(":llm/call", model="x", messages=[])
    assert out == {"text": "ok"}
    assert seen[0][0] == {"model": "x", "messages": []}


def test_outer_handler_dispatched_first_and_can_call_k():
    """Outer handler receives first; calling k drops to the next handler."""
    trace: list = []

    def outer(args, k, ctx):
        trace.append("outer-before")
        r = k(args)
        trace.append("outer-after")
        return {"wrapped": r}

    def inner(args, k, ctx):
        trace.append("inner")
        return {"raw": args["msg"]}

    rt = Runtime([
        Handler(name="inner", wraps={":llm/call"}, clauses={":llm/call": inner}),
        Handler(name="outer", wraps={":llm/call"}, clauses={":llm/call": outer}),
    ])
    with with_runtime(rt):
        out = perform(":llm/call", msg="hi")
    assert out == {"wrapped": {"raw": "hi"}}
    assert trace == ["outer-before", "inner", "outer-after"]


def test_handler_not_in_wraps_is_skipped():
    """A handler whose wraps set does not include the op is bypassed."""
    def policy(args, k, ctx):
        raise AssertionError("should not be called for :llm/call")

    def raw(args, k, ctx):
        return {"text": "pass-through"}

    rt = Runtime([
        Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw}),
        Handler(name="policy", wraps={":decide"}, clauses={":decide": policy}),
    ])
    with with_runtime(rt):
        out = perform(":llm/call")
    assert out == {"text": "pass-through"}


# ---------- Proposition 2: well-formedness ----------


def test_runtime_is_well_formed_when_every_catalog_op_has_a_handler():
    def noop(args, k, ctx):
        return None

    # Catalog of two ops, each covered.
    catalog = {":llm/call", ":tool/call"}
    rt = Runtime([
        Handler(name="h1", wraps={":llm/call", ":tool/call"}, clauses={":llm/call": noop, ":tool/call": noop}),
    ])
    assert rt.is_well_formed(catalog) is True


def test_runtime_is_not_well_formed_when_op_uncovered():
    def noop(args, k, ctx):
        return None

    catalog = {":llm/call", ":tool/call"}
    rt = Runtime([
        Handler(name="h1", wraps={":llm/call"}, clauses={":llm/call": noop}),
    ])
    assert rt.is_well_formed(catalog) is False


def test_missing_uncovered_ops_are_reported():
    def noop(args, k, ctx):
        return None

    catalog = {":llm/call", ":tool/call", ":mem/read"}
    rt = Runtime([Handler(name="h", wraps={":llm/call"}, clauses={":llm/call": noop})])
    missing = rt.uncovered_ops(catalog)
    assert missing == {":tool/call", ":mem/read"}


# ---------- mask (Koka-style) ----------


def test_mask_hides_named_handler_for_body():
    """mask(name) skips the named handler for any effect performed inside."""
    calls: list = []

    def audit(args, k, ctx):
        calls.append("audit")
        return k(args)

    def raw(args, k, ctx):
        return {"text": "hello"}

    rt = Runtime([
        Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw}),
        Handler(name="audit", wraps={":llm/call"}, clauses={":llm/call": audit}),
    ])
    with with_runtime(rt):
        with mask("audit"):
            out = perform(":llm/call", model="x")
        # outside mask, audit is live again
        out2 = perform(":llm/call", model="y")
    assert out == {"text": "hello"}
    assert out2 == {"text": "hello"}
    # audit only fired on the second (unmasked) call
    assert calls == ["audit"]


def test_mask_stacks_nested():
    """Masking an already-masked handler is a no-op; order of exit restores."""
    calls: list = []

    def audit(args, k, ctx):
        calls.append("audit")
        return k(args)

    def policy(args, k, ctx):
        calls.append("policy")
        return k(args)

    def raw(args, k, ctx):
        return 42

    rt = Runtime([
        Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw}),
        Handler(name="audit", wraps={":llm/call"}, clauses={":llm/call": audit}),
        Handler(name="policy", wraps={":llm/call"}, clauses={":llm/call": policy}),
    ])
    with with_runtime(rt):
        with mask("audit"):
            with mask("policy"):
                perform(":llm/call")  # both masked → no handler fires
            perform(":llm/call")  # only audit masked → policy fires
        perform(":llm/call")  # nothing masked → policy outermost, then audit
    # Stack (inner→outer) = [raw, audit, policy]; policy is outermost.
    # Call 1: both masked    → []
    # Call 2: audit masked   → ["policy"]
    # Call 3: nothing masked → ["policy", "audit"]
    assert calls == ["policy", "policy", "audit"]


# ---------- named handlers ----------


def test_named_dispatches_to_specific_handler_by_name():
    """named('audit-archive') addresses a handler by its name attribute."""
    hits: list = []

    def primary(args, k, ctx):
        hits.append("primary")
        return k(args)

    def archive(args, k, ctx):
        hits.append("archive")
        return {"archived": True}

    rt = Runtime([
        Handler(name="primary", wraps={":audit/emit"}, clauses={":audit/emit": primary}),
        Handler(name="archive", wraps={":audit/emit"}, clauses={":audit/emit": archive}),
    ])
    with with_runtime(rt):
        out = named("archive", ":audit/emit", kind="trace", payload={})
    assert out == {"archived": True}
    assert hits == ["archive"]


def test_named_raises_when_handler_missing():
    rt = Runtime([])
    with pytest.raises(Unhandled):
        with with_runtime(rt):
            named("does-not-exist", ":audit/emit", kind="x", payload={})


# ---------- effect object ----------


def test_effect_carries_op_and_args():
    e = Effect(":llm/call", {"model": "x"})
    assert e.op == ":llm/call"
    assert e.args == {"model": "x"}
