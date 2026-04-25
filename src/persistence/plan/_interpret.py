"""Back-compat re-export shim. The walker lives in `_walk.py` as of v0.4.

This shim keeps any existing import path `from persistence.plan._interpret import walk`
working; it will be removed in v0.5 after grep-verifying zero in-tree references.
"""
from persistence.plan._walk import walk

__all__ = ["walk"]
