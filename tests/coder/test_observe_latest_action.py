"""Phase 2.2b G5a — _prompt.build_messages LD3 widening tests.

Verifies that the LATEST action in obs.recent_actions is rendered
verbatim (bypassing the [:200] truncation that older history entries
keep). This fixes the "blind on its own outputs" failure mode where
stdout/stderr sentinels and tracebacks were truncated before reaching
the LLM.

Tests construct Observation directly via the dataclass constructor
(NOT via _observe() round-trip) — keeps tests fast and deterministic.
"""
from __future__ import annotations


def test_stdout_sentinel_reaches_prompt_verbatim():
    """LD3: stdout content >200 chars must reach the prompt unchopped."""
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    sentinel = "SENTINEL_STDOUT_2_2_B_AAAA"
    long_tail = "x" * 1000
    obs = Observation(
        iter_count=1,
        recent_decisions=(),
        recent_actions=(
            {
                "op": ":code/run",
                "args_hash": "h",
                "result_summary": {
                    "stdout": sentinel + long_tail,
                    "stderr": "",
                    "exit_code": 0,
                },
                "error": None,
                "latency_ms": 1,
            },
        ),
    )
    body = build_messages("task", obs)[0]["content"]
    # Sentinel must reach the LLM verbatim
    assert sentinel in body, "stdout sentinel missing from prompt"
    # And so must content >200 chars deep — proves the [:200] cap was bypassed
    deep_marker = "x" * 500
    assert deep_marker in body, "stdout content past 200 chars was truncated"


def test_stderr_sentinel_reaches_prompt_verbatim():
    """LD3: stderr content >200 chars must reach the prompt unchopped."""
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    sentinel = "SENTINEL_STDERR_2_2_B_BBBB"
    long_tail = "y" * 1000
    obs = Observation(
        iter_count=1,
        recent_decisions=(),
        recent_actions=(
            {
                "op": ":code/run",
                "args_hash": "h",
                "result_summary": {
                    "stdout": "",
                    "stderr": sentinel + long_tail,
                    "exit_code": 1,
                },
                "error": None,
                "latency_ms": 1,
            },
        ),
    )
    body = build_messages("task", obs)[0]["content"]
    assert sentinel in body, "stderr sentinel missing from prompt"
    deep_marker = "y" * 500
    assert deep_marker in body, "stderr content past 200 chars was truncated"


def test_exception_path_result_summary_none_renders_placeholder():
    """LD3: result_summary=None (exception path) must render a placeholder
    plus the error string — must not crash."""
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    obs = Observation(
        iter_count=1,
        recent_decisions=(),
        recent_actions=(
            {
                "op": ":fs/read",
                "args_hash": "h",
                "result_summary": None,
                "error": "FileNotFoundError: foo.txt",
                "latency_ms": 1,
            },
        ),
    )
    body = build_messages("task", obs)[0]["content"]
    assert "FileNotFoundError: foo.txt" in body
    assert "(no result_summary" in body


def test_list_shape_collapsing_for_glob_matches():
    """LD3: list-valued result_summary fields collapse to count + first_3
    in the LATEST action section so 100-match globs don't blow up cost.

    (The older 'Recent loop history' block still JSON-dumps the action
    with a [:200] cap, which may incidentally retain short paths — that
    is the OLDER block's contract, not the LD3 contract under test.)
    """
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    matches = [
        "a.txt", "b.txt", "c.txt", "d.txt", "e.txt",
        "f.txt", "g.txt", "h.txt", "i.txt", "j.txt",
    ]
    obs = Observation(
        iter_count=1,
        recent_decisions=(),
        recent_actions=(
            {
                "op": ":fs/glob",
                "args_hash": "h",
                "result_summary": {"matches": matches},
                "error": None,
                "latency_ms": 1,
            },
        ),
    )
    body = build_messages("task", obs)[0]["content"]
    assert "count=10" in body
    assert "first_3=" in body
    # Isolate the LATEST action section (between its header and the
    # 'Recent loop history' header) and assert tail elements were
    # collapsed there. Tail elements may still appear in the older
    # history block — that block is unchanged by LD3.
    head = body.index("Latest action output:")
    tail = body.index("Recent loop history")
    latest_section = body[head:tail]
    assert "j.txt" not in latest_section
    assert "h.txt" not in latest_section
    # And first_3 preview must contain the first three
    assert "a.txt" in latest_section
    assert "c.txt" in latest_section


def test_iter_zero_omits_latest_action_section():
    """LD3: with no actions yet (iter 0, empty tuples), the latest-action
    section must be omitted entirely — zero prompt overhead on first iter."""
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    obs = Observation()
    body = build_messages("task", obs)[0]["content"]
    assert "Latest action output" not in body


def test_iter_n_with_actions_shows_latest_action_section():
    """LD3: with actions present, the section header must appear."""
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    obs = Observation(
        iter_count=3,
        recent_decisions=(),
        recent_actions=(
            {
                "op": ":fs/read",
                "args_hash": "h",
                "result_summary": {"text": "hello"},
                "error": None,
                "latency_ms": 1,
            },
        ),
    )
    body = build_messages("task", obs)[0]["content"]
    assert "Latest action output" in body
