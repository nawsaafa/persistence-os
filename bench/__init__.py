"""Benchmark suites for persistence-os.

Each subpackage is an independent benchmark; they share no code or state.

* ``regulator_replay`` — synthetic 50-trajectory CC-BY-4.0 audit-replay corpus
  (Stream F, shipped at v0.7.0a1).

This module is the package marker that lets ``pytest`` import
``bench.<benchmark>.tests.test_*`` modules with their absolute ``from
bench.<benchmark> import ...`` statements; do not put benchmark logic here.
"""
