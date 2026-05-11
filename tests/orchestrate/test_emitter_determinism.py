"""G1 — LD-2 falsifier: emitter is byte-deterministic.

D-3 Mixed: minimal Python emitter using stdlib string-building +
existing edn_format parser. Same chain → byte-identical 4-file tree
across runs. Determinism falsifies if emit functions introduce
wall-clock, random, or dict-iteration-order leak.
"""
from __future__ import annotations

import filecmp
from pathlib import Path

from persistence.orchestrate import emit_orchestrator_skill
from persistence.orchestrate._schema import parse_chain_edn

CANNED_CHAIN_EDN = """(:chain
  :name "g1-determinism"
  :description "G1 falsifier chain"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "in.md"}
                :capability (:Capability :op "coder" :qualifier "read"))
          (:step :id 2
                :op :fs/write
                :args {:path "out.md"}
                :capability (:Capability :op "coder" :qualifier "write"))])"""


def test_emits_identical_tree_from_same_chain(tmp_path: Path) -> None:
    chain = parse_chain_edn(CANNED_CHAIN_EDN)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    emit_orchestrator_skill(chain, out_a)
    emit_orchestrator_skill(chain, out_b)

    for relpath in ["SKILL.md", "chain.edn", "preflight.toml", "orchestrate.py"]:
        assert (out_a / relpath).exists(), f"missing {relpath} in run a"
        assert (out_b / relpath).exists(), f"missing {relpath} in run b"
        assert filecmp.cmp(out_a / relpath, out_b / relpath, shallow=False), \
            f"emitter non-deterministic on {relpath}"
