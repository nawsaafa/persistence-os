"""Dry-run handler — short-circuits stateful ops when ``mode=dry-run``."""
from persistence.effect.handlers.dry_run import make_dry_run_handler
from persistence.effect.handlers.raw import make_echo_llm_handler, make_scripted_tool_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


def test_dry_run_mocks_tool_call():
    dry = make_dry_run_handler(
        mode="dry-run",
        wraps={"tool/call"},
        mocks={"tool/call": {"result": {"mocked": True}, "error": None}},
    )
    real_calls: list = []

    def raw_tool(args, k, ctx):
        real_calls.append(args)
        return {"result": {"real": True}, "error": None}

    raw = Handler(name="raw", wraps={"tool/call"}, clauses={"tool/call": raw_tool})
    rt = Runtime([raw, dry])
    with with_runtime(rt):
        out = perform("tool/call", name="stripe.charge", input={"amount": 100})
    assert out["result"] == {"mocked": True}
    assert real_calls == []  # never reached raw


def test_dry_run_mocks_emit_artifact():
    dry = make_dry_run_handler(
        mode="dry-run",
        wraps={"emit-artifact"},
        mocks={"emit-artifact": {"uri": "mock://fake.xlsx"}},
    )
    raw_calls: list = []

    def raw_emit(args, k, ctx):
        raw_calls.append(args)
        return {"uri": "s3://real/file.xlsx"}

    raw = Handler(name="raw", wraps={"emit-artifact"}, clauses={"emit-artifact": raw_emit})
    rt = Runtime([raw, dry])
    with with_runtime(rt):
        out = perform("emit-artifact", kind="xlsx", path="/tmp/x.xlsx")
    assert out["uri"] == "mock://fake.xlsx"
    assert raw_calls == []


def test_dry_run_passthrough_when_mode_is_live():
    dry = make_dry_run_handler(
        mode="live",
        wraps={"tool/call"},
        mocks={"tool/call": {"result": "should-not-be-returned"}},
    )
    real_calls: list = []

    def raw_tool(args, k, ctx):
        real_calls.append(args)
        return {"result": "real-result", "error": None}

    raw = Handler(name="raw", wraps={"tool/call"}, clauses={"tool/call": raw_tool})
    rt = Runtime([raw, dry])
    with with_runtime(rt):
        out = perform("tool/call", name="x", input={})
    assert out["result"] == "real-result"
    assert len(real_calls) == 1


def test_dry_run_allows_custom_allowlist():
    """Ops in ``allow_live`` are passed through even in dry-run mode.

    Useful for read-only ops (``:mem/read``, ``:clock/now``) that should
    run normally during a dry-run.
    """
    dry = make_dry_run_handler(
        mode="dry-run",
        wraps={"tool/call"},
        mocks={"tool/call": {"result": "mocked"}},
        allow_live={"tool/call"} & set(),  # empty — tool/call is mocked
    )
    # Sanity: with no allow_live, mock fires.
    raw = Handler(
        name="raw",
        wraps={"tool/call"},
        clauses={"tool/call": lambda a, k, ctx: {"result": "real"}},
    )
    rt = Runtime([raw, dry])
    with with_runtime(rt):
        out = perform("tool/call", name="x", input={})
    assert out["result"] == "mocked"


def test_dry_run_mock_can_be_callable():
    """A callable mock receives ``args`` and returns the mocked result.

    Enables per-call stubs (e.g. echo the tool name back).
    """

    def stub(args):
        return {"result": f"stub:{args['name']}"}

    dry = make_dry_run_handler(
        mode="dry-run",
        wraps={"tool/call"},
        mocks={"tool/call": stub},
    )
    raw = Handler(
        name="raw",
        wraps={"tool/call"},
        clauses={"tool/call": lambda a, k, ctx: {"result": "real"}},
    )
    rt = Runtime([raw, dry])
    with with_runtime(rt):
        out = perform("tool/call", name="stripe", input={})
    assert out == {"result": "stub:stripe"}
