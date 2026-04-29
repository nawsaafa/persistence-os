"""Determinism + shape tests for the regulator-replay corpus generator.

These tests use a *shrunken* op-count (``length=10`` instead of the corpus
default 40) so they run in well under a second per seed. The full 50-trajectory
corpus is exercised via the harness CLI; here we only need to pin:

* same-seed re-runs produce byte-identical files (this is the load-bearing
  contract that ``MANIFEST.json`` corpus-root + per-trajectory SHA-256s
  rest on);
* every trajectory contains the three mandatory intent tags
  (``retraction`` / ``tier_change`` / ``retroactive``) at least once
  (the regulatory-shape requirement encoded in :mod:`generator._plan_ops`);
* the audit chain returned from in-memory generation verifies via
  :func:`persistence.effect.verify_chain` (Prop 1 contract).
"""
from __future__ import annotations

import json
from pathlib import Path

from persistence.effect import verify_chain

from bench.regulator_replay import generator as gen


def test_generate_trajectory_is_byte_deterministic() -> None:
    """Two in-process calls on the same seed produce identical bytes."""
    entries_a, records_a = gen.generate_trajectory(seed=0, length=10)
    entries_b, records_b = gen.generate_trajectory(seed=0, length=10)

    blob_a_audit = "\n".join(gen._entry_to_jsonl(e) for e in entries_a)
    blob_b_audit = "\n".join(gen._entry_to_jsonl(e) for e in entries_b)
    assert blob_a_audit == blob_b_audit

    blob_a_log = gen._records_to_jsonl(records_a)
    blob_b_log = gen._records_to_jsonl(records_b)
    assert blob_a_log == blob_b_log


def test_write_trajectory_round_trip_byte_identity(tmp_path: Path) -> None:
    """Two write_trajectory runs into different dirs produce byte-identical files."""
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    bundle_a = gen.write_trajectory(seed=1, out_dir=a_dir, length=10)
    bundle_b = gen.write_trajectory(seed=1, out_dir=b_dir, length=10)

    for key in ("audit", "log"):
        bytes_a = bundle_a[key].read_bytes()
        bytes_b = bundle_b[key].read_bytes()
        assert bytes_a == bytes_b, f"{key} files diverged for seed=1"


def test_trajectory_has_three_mandatory_intents(tmp_path: Path) -> None:
    """Each trajectory carries ≥1 retraction, ≥1 tier_change, ≥1 retroactive op."""
    bundle = gen.write_trajectory(seed=2, out_dir=tmp_path, length=10)
    log_lines = bundle["log"].read_text(encoding="utf-8").strip().splitlines()
    intents: set[str] = set()
    for line in log_lines:
        rec = json.loads(line)
        if "intent" in rec and rec["intent"] in {"retraction", "tier_change", "retroactive"}:
            intents.add(rec["intent"])
    assert intents == {"retraction", "tier_change", "retroactive"}, (
        f"missing mandatory intents (have {intents}) for seed=2"
    )


def test_audit_chain_verifies() -> None:
    """Generated audit entries pass ``verify_chain`` — Prop 1 contract."""
    entries, _ = gen.generate_trajectory(seed=3, length=10)
    assert verify_chain(entries) is True
