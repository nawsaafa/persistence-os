"""G4 — LD-4 falsifier: emitted SKILL.md prereqs region is verbatim.

R0-fold B1+I2:
- clone+uv-sync clause is verbatim from positioning.md:98
- mintkey/UNSET clause is NEW extension grounded in CLAUDE.md:32 +
  positioning.md:21-23
- markers <!-- prereqs-begin --> / <!-- prereqs-end --> wrap the region
- exact string equality on extraction
"""
from __future__ import annotations

import re
from pathlib import Path

from persistence.orchestrate import (
    PREREQS_REGION,
    emit_orchestrator_skill,
    parse_chain_edn,
)

CANNED_CHAIN_EDN = """(:chain
  :name "g4-prereqs"
  :description "G4 falsifier"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "x.md"}
                :capability (:Capability :op "coder" :qualifier "read"))])"""


def test_emitted_skill_md_contains_verbatim_install_block(tmp_path: Path) -> None:
    chain = parse_chain_edn(CANNED_CHAIN_EDN)
    emit_orchestrator_skill(chain, tmp_path)

    md = (tmp_path / "SKILL.md").read_text()

    # Markers appear exactly once each
    assert md.count("<!-- prereqs-begin -->") == 1, \
        "prereqs-begin marker must appear exactly once"
    assert md.count("<!-- prereqs-end -->") == 1, \
        "prereqs-end marker must appear exactly once"

    # Extract region between markers and assert exact equality
    match = re.search(
        r"<!-- prereqs-begin -->\n(.*?)\n<!-- prereqs-end -->",
        md, re.DOTALL,
    )
    assert match is not None, "prereqs markers must be present in emitted SKILL.md"
    extracted = match.group(1)
    assert extracted == PREREQS_REGION, (
        f"prereqs region drift:\n--- expected ---\n{PREREQS_REGION!r}\n"
        f"--- got ---\n{extracted!r}"
    )

    # Cross-check: clone+uv-sync clause is verbatim in positioning.md
    positioning_md = (
        Path(__file__).parent.parent.parent
        / "docs/release-notes/v0.9.0a1-positioning.md"
    ).read_text()
    install_sentence = "Clone `persistence-os`, run `uv sync`"
    assert install_sentence in positioning_md, (
        "positioning.md drifted: missing canonical install sentence"
    )
    assert install_sentence in PREREQS_REGION, (
        "PREREQS_REGION must include the canonical install sentence"
    )

    # Negative controls (LD-4 codex-fold pass 2)
    assert "pip install git+" not in md, \
        "regression: pre-Phase-E posture violated"
    assert "PERSISTENCE_AUDIT_KEY=\n" not in md, \
        "regression: empty-string env-unset is undocumented"
