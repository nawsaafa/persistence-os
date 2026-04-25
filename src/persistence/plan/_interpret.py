"""Back-compat re-export shim. The walker lives in `_walk.py` as of v0.4.

This shim keeps any existing import path `from persistence.plan._interpret import walk`
working; it will be removed in v0.5 after grep-verifying zero in-tree references.

Scope note: only `walk` is re-exported. Module-private constants like
`_UNIMPLEMENTED_KINDS` and `_UPGRADE_MESSAGES` were never part of the
public surface and are NOT re-exported — import from `_walk` directly
if you need them.
"""
from persistence.plan._walk import walk

__all__ = ["walk"]
