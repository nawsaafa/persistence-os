"""Regulator-replay benchmark — synthetic CC-BY-4.0 corpus + replay harness.

See ``README.md`` for the auditor-facing brief and ``MANIFEST.json`` for
corpus identity. The two callable surfaces are :mod:`bench.regulator_replay.generator`
(produces the corpus) and :mod:`bench.regulator_replay.harness` (replays
audit chains and emits a tamper-detection report).
"""
from __future__ import annotations

CORPUS_VERSION = "1.0.0"
"""Bumped when the generator changes the on-disk shape of trajectories.

The substrate version is read from :mod:`persistence` at runtime; this
constant is the corpus-format version that lets future readers detect a
shape break separately from a substrate version bump.
"""

__all__ = ["CORPUS_VERSION"]
