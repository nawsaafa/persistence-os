"""Phase A — verify _walk.py is the new home and _interpret.py is a shim."""
from __future__ import annotations


def test_walk_module_exists_and_exports_walk():
    """_walk.py is the new canonical home for the walker."""
    from persistence.plan import _walk
    assert hasattr(_walk, "walk")
    assert callable(_walk.walk)


def test_interpret_module_back_compat_shim_re_exports_walk():
    """_interpret.py keeps working as a back-compat shim that re-exports walk."""
    from persistence.plan import _interpret
    assert hasattr(_interpret, "walk")
    assert callable(_interpret.walk)
    # Must be the SAME function object — not a re-implementation
    from persistence.plan import _walk
    assert _interpret.walk is _walk.walk


def test_top_level_walk_import_unchanged():
    """The public `from persistence.plan import walk` keeps working."""
    from persistence.plan import walk
    from persistence.plan._walk import walk as walk_from_walk_module
    assert walk is walk_from_walk_module
