"""A1 — Execution core MUST be importable without `dspy` installed.

This test fences the design contract per
docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §3:

  > Execution core (`_execute.py`) is split from optimizer (`_optimize.py`).
  > [...] the execution core must remain importable without DSPy installed
  > (and without DSPy ever entering its import path). [...]
  > Test suite enforces: `python -c "import persistence.plan._execute"`
  > succeeds with `dspy-ai` uninstalled.

We can't actually uninstall dspy in the test process, so we use a subprocess
that pre-poisons `sys.modules['dspy']=None` — any `import dspy` inside
`_execute` would then fail with ImportError. If `_execute` imports cleanly,
we know it does not pull dspy on its module import path.

Also tests the `from persistence.plan import execute` public surface under
the same fence.
"""
from __future__ import annotations

import subprocess
import sys


def test_execute_module_imports_without_dspy():
    """`import persistence.plan._execute` succeeds with `sys.modules['dspy']=None`.

    sys.modules[name] = None makes subsequent `import name` raise ImportError,
    so any `import dspy` (module-level or function-level reached at import
    time) anywhere on the _execute import path would fail.
    """
    code = (
        "import sys\n"
        "sys.modules['dspy'] = None\n"
        "import persistence.plan._execute\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ok" in result.stdout


def test_execute_public_re_export_imports_without_dspy():
    """`from persistence.plan import execute` works without dspy on import path.

    The re-export from __init__.py must not pull _optimize.py (which is
    DSPy-dependent). Verifies __init__.py doesn't accidentally drag the
    optimizer into the execution-core import path.
    """
    code = (
        "import sys\n"
        "sys.modules['dspy'] = None\n"
        "from persistence.plan import execute, ExecutionResult, LeafResult, FailureInfo\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ok" in result.stdout


def test_execute_runs_without_dspy_in_module_path():
    """A real `execute()` call goes through with dspy module-poisoned.

    Belt-and-suspenders: import alone is one thing; calling execute() with
    a tiny plan exercises the full call path and must also not touch dspy.
    """
    code = (
        "import sys\n"
        "sys.modules['dspy'] = None\n"
        "from persistence.plan import Dispatcher, Node, execute\n"
        "leaf = Node(tag=':llm-call')\n"
        "d = Dispatcher()\n"
        "d.register(':llm-call', lambda n, env: 'ok')\n"
        "res = execute(leaf, d)\n"
        "assert res.status == 'ok'\n"
        "assert res.leaf_results[0].result == 'ok'\n"
        "print('done')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "done" in result.stdout
