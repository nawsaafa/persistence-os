"""Replay + tamper-detection tests for the regulator-replay harness.

Generates a 3-seed mini-corpus into ``tmp_path``, then exercises:

* :func:`harness.replay_one` — every chain must verify on a clean file;
* :func:`harness.tamper_check_one` — flipping a single byte at a sampled
  offset must be caught (parse failure or Merkle rejection);
* :func:`harness.run` — end-to-end report shape: ``n_pass == n_trajectories``,
  ``audit_reconstructibility == 1.0``, ``tamper_matrix.all_caught is True``.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench.regulator_replay import generator as gen
from bench.regulator_replay import harness


def _seed_mini_corpus(out_dir: Path) -> list[dict[str, Path]]:
    """Generate seeds 0..2 with length=10 — fast, still hits all intents."""
    return [
        gen.write_trajectory(seed=s, out_dir=out_dir, length=10)
        for s in range(3)
    ]


def test_replay_one_accepts_clean_chain(tmp_path: Path) -> None:
    bundles = _seed_mini_corpus(tmp_path)
    for seed, bundle in enumerate(bundles):
        result = harness.replay_one(bundle["audit"], seed=seed)
        assert result.chain_valid is True, (
            f"seed={seed} unexpectedly failed: {result.error!r}"
        )
        assert result.n_entries > 0
        assert result.error is None


def test_tamper_check_catches_byte_flip(tmp_path: Path) -> None:
    bundles = _seed_mini_corpus(tmp_path)
    audit_path = bundles[0]["audit"]
    assert audit_path.stat().st_size > 10

    # ``tamper_check_one`` flips one byte at each sampled offset and
    # returns one result per sample. Whether a flip lands on the JSON
    # envelope or the Merkle hash, both paths must raise the
    # ``caught`` flag — the contract Prop 1 enforces.
    results = harness.tamper_check_one(audit_path, seed=0)
    assert len(results) > 0
    missed = [r for r in results if not r.caught]
    assert missed == [], (
        f"tamper not caught at offsets={[r.byte_offset for r in missed]}"
    )


def test_run_end_to_end_report_shape(tmp_path: Path) -> None:
    in_dir = tmp_path / "trajectories"
    out_dir = tmp_path / "reports"
    _seed_mini_corpus(in_dir)

    report = harness.run(in_dir, out_dir)

    assert report["n_trajectories"] == 3
    assert report["n_pass"] == 3
    assert report["n_fail"] == 0
    assert report["audit_reconstructibility"] == 1.0
    assert report["tamper_matrix"]["all_caught"] is True
    assert report["tamper_matrix"]["missed"] == 0
    # ``samples_per_file`` is the fractional-position list the harness
    # samples; the absolute flip count = len(samples) × 3 trajectories.
    expected_flips = len(report["tamper_matrix"]["samples_per_file"]) * 3
    assert report["tamper_matrix"]["caught"] == expected_flips
    assert report["tamper_matrix"]["total_flips"] == expected_flips

    # Report file landed and is valid JSON.
    report_files = list(out_dir.glob("run-*.json"))
    assert len(report_files) == 1
    parsed = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert parsed["n_pass"] == 3
