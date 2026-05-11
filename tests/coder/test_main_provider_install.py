"""Phase 2.1b / 2.4b.1 — __main__.py provider install + stderr UX tests.

Subprocess-based: launches `python -m persistence.coder` in a fresh
process with controlled env, captures stderr. Hermetic — passes
env= and cwd= explicitly per the 2.1a F1 BLOCKING fix lesson.

Phase 2.4b.1 LD-1 G2 (in-process): the non-echo ValueError regression
guard calls ``main()`` directly with monkey-patched provider detection
+ Coder so the except-clause's narrow-mask logic is exercised without
spawning a subprocess.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from persistence.coder import __main__ as coder_main
from persistence.coder import _provider as _provider_mod


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = str(REPO_ROOT / "src")


def _run(argv, env_extra: dict | None = None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": PYTHONPATH}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "persistence.coder"] + argv,
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_auto_no_providers_prints_echo_warning_and_exits_one():
    """No ANTHROPIC_API_KEY, no claude-agent-sdk → echo floor + warning +
    LD-1 banner-mask + exit 1 (no raw traceback).

    Phase 2.4b.1 LD-1 (codex consensus Option C): the echo handler can't
    drive a real agent loop. Post-2.3b, the no-provider path eventually
    raises ValueError from inside ``Coder.run()`` because echo's
    deterministic ``{"text": "echo:..."}`` response falls through
    ``_session._decide`` to a tier-3 fallback with no ``op`` field, and
    ``_act()`` raises. ``__main__.main()`` narrow-masks this case
    (guarded by ``provider_name == "echo"``) so the user sees a single-
    line stderr banner instead of a raw Python traceback.
    """
    # Skip if claude-agent-sdk is installed — auto would pick claude-code, not echo.
    # The subprocess inherits the venv's site-packages because sys.executable is
    # the venv's python; `claude_agent_sdk` is therefore importable in the child.
    try:
        import claude_agent_sdk  # noqa: F401
        pytest.skip("claude-agent-sdk installed; auto path picks it instead of echo")
    except ImportError:
        pass
    result = _run(["--task", "t"])
    assert result.returncode == 1
    # Pre-loop warnings from _build_substrate_and_handlers (__main__.py:166-170).
    assert "no LLM provider available" in result.stderr
    assert "echo handler" in result.stderr
    # Phase 2.4b.1 LD-1 narrow-mask banner — exact substrings per R0-fold I4
    # (no "or similar" wording). If the banner message drifts, this fails.
    assert (
        "persistence-coder: echo handler can't drive a real agent loop"
        in result.stderr
    )
    assert (
        "Set ANTHROPIC_API_KEY or sign in to Claude Code, then re-run."
        in result.stderr
    )
    # Narrow-mask guarantee — if the LD-1 ``except ValueError`` block is
    # forgotten, this would re-leak the raw Python traceback that
    # users saw pre-2.4b.1.
    assert "Traceback (most recent call last):" not in result.stderr


def test_main_explicit_anthropic_no_key_exits_with_error():
    result = _run(["--task", "t", "--provider", "anthropic"])
    assert result.returncode != 0
    assert "ANTHROPIC_API_KEY not set" in result.stderr


def test_main_explicit_claude_code_no_sdk_exits_with_error():
    result = _run(["--task", "t", "--provider", "claude-code"])
    # Skip if claude-agent-sdk IS installed — this test covers the absent case
    if "not installed" not in result.stderr:
        pytest.skip("claude-agent-sdk is installed in this env; skipping absent-case test")
    assert result.returncode != 0
    assert "claude-agent-sdk not installed" in result.stderr


def test_main_provider_stderr_note_for_anthropic_when_key_present():
    """When key is set, stderr says 'using anthropic provider'."""
    pytest.importorskip("anthropic")
    result = _run(
        ["--task", "t", "--provider", "anthropic"],
        env_extra={"ANTHROPIC_API_KEY": "sk-ant-fake-no-real-call"},
    )
    # The fake API key triggers a downstream error in the anthropic provider
    # before any real network call; the test only checks the stderr provider
    # note printed at __main__.py:173 (path taken when provider_name != "echo").
    # Codex Impl R1 I1 fold: prior comment was post-2.3b stale framing.
    assert "using anthropic provider" in result.stderr


# ---------------------------------------------------------------------------
# Phase 2.4b.1 LD-1 G2 — non-echo ValueError must NOT be banner-masked
# ---------------------------------------------------------------------------


class _ValueErrorCoderStub:
    """Stand-in for ``persistence.coder.__main__.Coder`` that raises
    ``ValueError`` from ``.run()``.

    G2 forces a programmer-error-shaped failure inside the inner try-block
    of ``main()`` so the ``except ValueError`` clause's ``provider_name ==
    "echo"`` guard is exercised on the NON-echo branch.
    """

    def __init__(self, task, substrate, model, max_iters):
        self.task = task
        self.substrate = substrate
        self.model = model
        self.max_iters = max_iters

    def run(self):  # noqa: D401 — simple stub
        raise ValueError("test G2: non-echo programmer error must propagate")


def _fake_non_echo_detect(_provider):
    """Pin detect_or_explicit to a NON-echo path with a harmless handler.

    Returning ``handler=None`` would route to ``make_echo_llm_handler``
    in ``_build_substrate_and_handlers`` AND co-locate the
    ``provider_name == "echo"`` label, defeating G2. Instead return a
    no-op callable handler labeled ``"anthropic"`` so the helper takes
    the non-echo branch and ``main()`` sees ``provider_name == "anthropic"``.
    """
    from persistence.effect.handlers.callable import make_callable_llm_handler

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        # Never reached in G2 — Coder is stubbed to raise before any :llm/call.
        return {"text": "stub"}

    return (make_callable_llm_handler(_call_fn, name="raw-echo"), "anthropic")


def test_non_echo_value_error_propagates_raw_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LD-1 G2 regression guard — the ValueError mask MUST be narrow.

    If a real programmer error (ValueError from non-echo path) is
    swallowed by the mask, future bugs will hide silently. This test
    forces a ValueError raise in non-echo mode and asserts the
    ValueError DOES propagate out of ``main()`` rather than being
    caught and converted to a return-1 + stderr banner.

    Falsifier (manual probe): widen the mask to catch ValueError
    regardless of ``provider_name`` (delete the ``if provider_name ==
    "echo":`` guard, keep the print + ``return 1``) and re-run this
    test. The ``pytest.raises(ValueError)`` block will fail because
    ``main()`` will return 1 instead of propagating — DID NOT RAISE.
    """
    # Pin both reference paths to a non-echo handler so the helper
    # tags provider_name="anthropic" regardless of test-machine env.
    monkeypatch.setattr(
        coder_main, "detect_or_explicit", _fake_non_echo_detect,
    )
    monkeypatch.setattr(
        _provider_mod, "detect_or_explicit", _fake_non_echo_detect,
    )

    # Replace Coder at the import-site in __main__ so .run() raises
    # ValueError inside the inner try-block of main(). The dataclass
    # Coder normally validates kwargs; our stub shape mirrors the
    # 4 fields main() passes (task, substrate, model, max_iters).
    monkeypatch.setattr(coder_main, "Coder", _ValueErrorCoderStub)

    # G2 core assertion: ValueError propagates out of main() instead
    # of being banner-masked. The exact message string is also a
    # tighter check that we hit the stub-raised ValueError (not some
    # other ValueError raised on the way down).
    with pytest.raises(
        ValueError,
        match="test G2: non-echo programmer error must propagate",
    ):
        coder_main.main(["--task", "t"])

    # Stderr must NOT contain the LD-1 echo banner — if it does, the
    # mask is firing on non-echo provider_name (broad-catch regression).
    captured = capsys.readouterr()
    assert (
        "persistence-coder: echo handler can't drive a real agent loop"
        not in captured.err
    ), (
        "LD-1 narrow-mask regression: echo banner printed in non-echo "
        "mode — mask is broader than `provider_name == \"echo\"`"
    )
    # Sanity-check the non-echo path was actually taken (helper prints
    # 'using anthropic provider' on the non-echo branch).
    assert "using anthropic provider" in captured.err
