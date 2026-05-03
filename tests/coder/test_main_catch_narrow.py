"""Phase 2.1a — direct regression test for R1 fix-1 narrow catch.

The CLI banner in `persistence.coder.__main__.main` must catch ONLY
`CoderStubNotImplemented`, NOT bare `NotImplementedError`. If a real
2.1b+ method body raises a vanilla `NotImplementedError` (a real bug,
not a Phase 2.1a stub sentinel), it must propagate out of `main()`
rather than being banner-masked + exit-1'd as if it were a normal
skeleton-not-yet-filled signal.

Without this test, a future refactor that broadens the `except` to
`NotImplementedError` would still pass every other test in the suite,
but would silently swallow real downstream bugs. F2 from impl ARIS R1.
"""

import pytest

from persistence.coder import _session
from persistence.coder.__main__ import main


def test_main_does_not_swallow_bare_not_implemented_error(monkeypatch):
    def fake_observe(self):
        raise NotImplementedError("simulated 2.1b real bug, NOT a stub")

    monkeypatch.setattr(_session.Coder, "_observe", fake_observe)

    with pytest.raises(NotImplementedError, match="simulated 2.1b real bug"):
        main(["--task", "trigger-fake-bug"])
