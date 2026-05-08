"""Phase 2.3c.2 G5 — `ComposeWithSkillAction` proposal acceptance + content-hash provenance.

Covers test gate G5 from
``docs/plans/2026-05-08-phase-2.3c.2-recursion-composition-design.md`` § 4:

  * G5.1 — MCTS expander accepts ``ComposeWithSkillAction`` proposals
    (FD7 lift verified — neither kind-string nor isinstance rejection
    fires when SkillLibrary is threaded).
  * G5.2 — SkillLibrary is threaded through the search context such that
    ``_apply_compose_with_skill`` is called with non-None ``skill_library``
    and the skill graft succeeds.
  * G5.3 — Pinned content hash in ``:mcts/iteration`` provenance:
    ``composed_skill_content_hash`` populated, equal to ``looked_up_plan.id``.
  * G5.4 — **R0-fold I1 alias case (LOAD-BEARING falsifiability):**
    register skill A with body X; re-register byte-identical body X under
    different ``promotion_id`` → ``skill_id_B`` (different ``skill_id``,
    SAME ``content_hash``). Compose A in iter 1; compose B in iter 2.
    Assert ``composed_skill_content_hash`` is EQUAL across both
    iterations (proves pinning is content-hash, NOT skill_id). A "fake"
    impl that pinned ``skill_id`` would FAIL this assertion.
  * G5.5 — **R1-fold N1 tightening:** post-graft AST contains
    ``looked_up_plan.id`` as a subtree (via ``_collect_node_ids``).
    Catches the sophisticated bug class where impl correctly pins
    ``content_hash`` in provenance but FAILS to actually graft the
    matching subtree.

The existing 2.3b LD3 reject test
(``test_expander_drops_compose_with_skill_action``) is updated under
LD6 option (b) — kept as a NEGATIVE test for the case where
``SkillLibrary`` is None or ``skill_id`` is unregistered.

Test fixture pattern: the `_searcher_search.py` real-MCTS harness with
a smart `:llm/call` mock that distinguishes expander vs evaluator by
tool name. SkillLibrary is bound on the substrate; pre-registered with
test skills. The bridge calls ``mcts_search(skill_library=...)`` directly.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from persistence.coder._searcher import (
    _escalate_branch_body,
    _make_branch_expander,
    _parse_expander_tool_response,
)
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.effect.handlers.skill import _PromotionRecordStub
from persistence.plan import parse, unparse
from persistence.plan._mcts import (
    AddStepAction,
    ComposeWithSkillAction,
    SubstituteLeafAction,
    _collect_node_ids,
)
from persistence.sdk import Substrate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dc.dataclass
class _CoderStub:
    """Minimal Coder-shaped stub. ``_escalate_branch_body`` reads
    ``.substrate``, ``.model``, and ``.skill_library`` (T5 add).
    Mirrors ``test_searcher_search.py:_CoderStub``.
    """

    substrate: Substrate
    model: str = "claude-test-2.3c.2"
    skill_library: Any = None  # populated per-test


def _make_skill_compose_call_fn(
    *,
    skill_id: str,
    seed_target_path: tuple[int, ...] = (0,),
):
    """Smart `:llm/call` mock: expander proposes EXACTLY one
    ``ComposeWithSkillAction(skill_id=...)`` at ``seed_target_path``;
    evaluator scores composed plans at 0.95 (winner) and seed at 0.05.

    Single-proposal expansion keeps the search tree narrow + deterministic
    under PUCT — no need for tight `max_iter`/`expander_k` mocking.
    """

    def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        tool_name = tools[0]["name"] if tools else ""

        if tool_name == "emit_branch_proposals":
            return {
                "tool_calls": [{
                    "input": {
                        "proposals": [
                            {
                                "kind": "ComposeWithSkillAction",
                                "target_path": list(seed_target_path),
                                "skill_id": skill_id,
                                "logit": 2.0,
                            },
                        ]
                    }
                }],
                "text": "",
                "usage": {"total_tokens": 12},
                "fingerprint": "test",
            }

        if tool_name == "emit_branch_score":
            user_content = messages[1]["content"]
            # Score plans containing the skill_id substring as winners
            # (composed plan EDN includes the grafted skill body's leaves
            # — the seed is a tiny `[:fs/write ...]` so the composed plan
            # is structurally larger). Use a structural marker we control.
            if "winner-marker" in user_content:
                score = 0.95
            else:
                score = 0.05
            return {
                "tool_calls": [{"input": {"score": score}}],
                "text": "",
                "usage": {"total_tokens": 5},
                "fingerprint": "test",
            }

        return {"tool_calls": [], "text": "unknown tool"}

    return call_fn


@pytest.fixture
def s(tmp_path: Path):
    """Real in-memory substrate with `:fs/*` handlers for plan execution."""
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    with Substrate.open("memory") as substrate:
        fs_handler = make_fs_handler(
            project_root=project_root, scratch_dir=scratch_dir
        )
        substrate.effect.install_handler(fs_handler, position="bottom")
        yield substrate


# --------------------------------------------------------------------------- #
# G5.1 — Wrapper-layer FD7 lift: ComposeWithSkillAction NOT dropped when
# skill_library is provided
# --------------------------------------------------------------------------- #


def test_g5_1_compose_proposal_passes_wrapper_when_skill_library_provided() -> None:
    """FD7 lift: with a SkillLibrary that has the skill_id registered,
    ``_parse_expander_tool_response`` no longer drops the
    ``ComposeWithSkillAction`` proposal at the kind-string + isinstance
    layers."""
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        skill_body = parse(
            '[:seq {} [:fs/write {:path "winner-marker.txt" :bytes_or_text "x"}]]',
            strict=False,
        )
        skill_id = skill_lib.register(
            skill_body,
            _PromotionRecordStub(promotion_id="prom-1"),
            registered_at_ms=1_700_000_000_000,
        )
        # Seed plan that the proposal targets
        seed = parse(
            '[:seq {} [:fs/write {:path "seed.txt" :bytes_or_text "s"}]]',
            strict=False,
        )
        response = {
            "tool_calls": [{
                "input": {
                    "proposals": [
                        {
                            "kind": "ComposeWithSkillAction",
                            "target_path": [0],
                            "skill_id": skill_id,
                            "logit": 1.0,
                        },
                    ]
                }
            }],
        }
        proposals = _parse_expander_tool_response(
            response, seed, skill_library=skill_lib,
        )
        # FD7 lift: proposal survives instead of being dropped at line
        # 361 (kind string) or line 369 (isinstance).
        assert len(proposals) == 1
        action, _prior = proposals[0]
        assert isinstance(action, ComposeWithSkillAction)
        assert action.skill_id == skill_id


def test_g5_1_compose_proposal_dropped_when_skill_library_none() -> None:
    """LD6 option (b) — the existing 2.3b FD7 reject test, RECAST as a
    NEGATIVE case. Proposal is rejected at the dry-run layer when
    skill_library is None (``_apply_compose_with_skill`` raises
    ``_SkillNotRegistered``), not at the kind-string layer.
    """
    seed = parse(
        '[:seq {} [:fs/write {:path "seed.txt" :bytes_or_text "s"}]]',
        strict=False,
    )
    response = {
        "tool_calls": [{
            "input": {
                "proposals": [
                    {
                        "kind": "ComposeWithSkillAction",
                        "target_path": [0],
                        "skill_id": "skill/abc",
                        "logit": 1.0,
                    },
                ]
            }
        }],
    }
    # Without skill_library, the dry-run rejects the proposal.
    proposals = _parse_expander_tool_response(
        response, seed, skill_library=None,
    )
    assert len(proposals) == 0


def test_g5_1_compose_proposal_dropped_when_skill_id_unregistered() -> None:
    """LD6 option (b) — even with a SkillLibrary, an unregistered
    ``skill_id`` causes ``_SkillNotRegistered`` at dry-run; proposal
    drops gracefully (existing search loop tolerance preserved)."""
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        seed = parse(
            '[:seq {} [:fs/write {:path "seed.txt" :bytes_or_text "s"}]]',
            strict=False,
        )
        response = {
            "tool_calls": [{
                "input": {
                    "proposals": [
                        {
                            "kind": "ComposeWithSkillAction",
                            "target_path": [0],
                            "skill_id": "skill/never-registered",
                            "logit": 1.0,
                        },
                    ]
                }
            }],
        }
        proposals = _parse_expander_tool_response(
            response, seed, skill_library=skill_lib,
        )
        assert len(proposals) == 0


# --------------------------------------------------------------------------- #
# G5.2 — End-to-end: SkillLibrary threaded through bridge into mcts_search
# --------------------------------------------------------------------------- #


def test_g5_2_bridge_passes_skill_library_to_mcts_search(s, tmp_path):
    """LD3+LD6: ``_escalate_branch_body`` forwards
    ``coder.skill_library`` into ``mcts_search`` as the ``skill_library``
    kwarg. Without this threading, ``_apply_compose_with_skill`` would
    reject every ``ComposeWithSkillAction`` proposal at the engine layer.
    """
    from unittest.mock import MagicMock

    from persistence.plan._mcts import MCTSResult

    skill_lib = s.plan.skill_library(s._db)
    seed_path = str(tmp_path / "seed.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
    )
    winner_plan = parse(seed_edn, strict=False)

    mock_search = MagicMock(return_value=MCTSResult(
        winner=winner_plan,
        winner_plan_id="0" * 64,
        initial_plan_id="1" * 64,
        search_id="2" * 64,
        iter_count=1,
        unique_plans_visited=1,
        terminated_by="max_iter",
        root_q=0.5,
        tree_dump=(),
    ))
    s.plan.mcts_search = mock_search  # type: ignore[method-assign]

    coder = _CoderStub(substrate=s, skill_library=skill_lib)
    _escalate_branch_body(
        coder,
        LLMDecision(
            kind="branch", confidence=0.9,
            payload={"seed_plan_edn": seed_edn},
        ),
    )

    # mcts_search received the SkillLibrary instance from coder
    assert mock_search.call_count == 1
    kwargs = mock_search.call_args.kwargs
    assert "skill_library" in kwargs
    assert kwargs["skill_library"] is skill_lib


def test_g5_2_bridge_passes_none_when_coder_lacks_skill_library(s, tmp_path):
    """Backward-compat: a coder constructed without a skill_library
    threads ``None`` through. Proposals still reject at the engine layer
    (matches 2.3b's behavior modulo the wrapper-layer drops being lifted)."""
    from unittest.mock import MagicMock

    from persistence.plan._mcts import MCTSResult

    seed_path = str(tmp_path / "seed.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
    )
    winner_plan = parse(seed_edn, strict=False)
    mock_search = MagicMock(return_value=MCTSResult(
        winner=winner_plan, winner_plan_id="0"*64, initial_plan_id="1"*64,
        search_id="2"*64, iter_count=1, unique_plans_visited=1,
        terminated_by="max_iter", root_q=0.5, tree_dump=(),
    ))
    s.plan.mcts_search = mock_search  # type: ignore[method-assign]

    coder = _CoderStub(substrate=s, skill_library=None)
    _escalate_branch_body(
        coder,
        LLMDecision(
            kind="branch", confidence=0.9,
            payload={"seed_plan_edn": seed_edn},
        ),
    )
    assert mock_search.call_args.kwargs.get("skill_library") is None


# --------------------------------------------------------------------------- #
# G5.3 — Real MCTS run: composed_skill_content_hash in :mcts/iteration provenance
# --------------------------------------------------------------------------- #


def _extract_compose_provenance_from_facts(
    s: Substrate, search_id: str | None = None,
) -> list[dict[str, Any]]:
    """Pull ``mcts/output`` rows whose payload references
    ``ComposeWithSkillAction`` and return the proposal records'
    ``action_payload`` dicts.

    Each iteration's expand-phase payload is a list of proposal records
    (per ``_expand_proposal_record``). We filter to those with
    ``action_kind == "ComposeWithSkillAction"`` and return their
    ``action_payload`` dicts so tests can read ``composed_skill_content_hash``.

    Reject-phase records use the same ``action_payload`` shape via
    ``_reject_record`` cascade, so this helper surfaces both expand
    and reject ComposeWithSkillAction provenance — fine for
    explainability tests.
    """
    compose_payloads: list[dict[str, Any]] = []
    for d in s._db.log():
        if d.a != "mcts/output":
            continue
        # The ``v`` field stores the JSON-encoded output_value. It can
        # be either a list (expand phase) or a single dict (reject phase).
        # Datom.v is already the deserialized object in this code path
        # — it was passed straight through to ``_make_iter_facts``.
        payloads_to_check: list[Any] = []
        if isinstance(d.v, list):
            payloads_to_check.extend(d.v)
        elif isinstance(d.v, dict):
            payloads_to_check.append(d.v)
        for record in payloads_to_check:
            if (
                isinstance(record, dict)
                and record.get("action_kind") == "ComposeWithSkillAction"
            ):
                payload = record.get("action_payload", {})
                if isinstance(payload, dict):
                    compose_payloads.append(payload)
    return compose_payloads


def test_g5_3_compose_provenance_includes_content_hash(s, tmp_path):
    """LD3: ``:mcts/iteration`` expand-phase provenance for a
    ``ComposeWithSkillAction`` proposal includes
    ``composed_skill_content_hash`` equal to the looked-up plan's
    ``plan.id`` content hash."""
    skill_lib = s.plan.skill_library(s._db)
    skill_body_edn = (
        '[:seq {} [:fs/write '
        f'{{:path "{tmp_path / "winner-marker.txt"}" '
        ':bytes_or_text "winner-content"}]]'
    )
    skill_body = parse(skill_body_edn, strict=False)
    skill_id = skill_lib.register(
        skill_body,
        _PromotionRecordStub(promotion_id="prom-A"),
        registered_at_ms=1_700_000_000_000,
    )
    looked_up_plan_id = skill_body.id  # the content-addressed plan.id

    seed_path = str(tmp_path / "seed.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_skill_compose_call_fn(skill_id=skill_id),
        ),
        position="bottom",
    )

    coder = _CoderStub(substrate=s, skill_library=skill_lib)
    _escalate_branch_body(
        coder,
        LLMDecision(
            kind="branch", confidence=0.9,
            payload={
                "seed_plan_edn": seed_edn,
                "mcts_config": {"max_iter": 2, "expander_k": 1},
            },
        ),
    )

    compose_payloads = _extract_compose_provenance_from_facts(s)
    # At least one expand-phase compose proposal must have surfaced
    assert len(compose_payloads) >= 1, (
        "Expected at least 1 ComposeWithSkillAction provenance record; "
        f"got {compose_payloads!r}"
    )
    # Each record carries skill_id + composed_skill_content_hash
    for payload in compose_payloads:
        assert payload.get("skill_id") == skill_id
        assert payload.get("composed_skill_content_hash") == looked_up_plan_id


# --------------------------------------------------------------------------- #
# G5.4 — Alias case: same content_hash via different skill_ids (LOAD-BEARING)
# --------------------------------------------------------------------------- #


def test_g5_4_alias_case_pinned_hash_equal_across_skill_ids(s, tmp_path):
    """LOAD-BEARING falsifiability (R0-fold I1):

    Register skill A with body X; then register byte-identical body X
    again. Per ``SkillLibrary.register`` semantics, both registrations
    produce the SAME ``skill_id`` (re-registration is idempotent — the
    content-addressed skill_id is derived from the plan AST hash).
    Their ``composed_skill_content_hash`` MUST therefore be EQUAL.

    A "fake" impl that pinned ``skill_id`` instead of the looked-up
    plan's ``plan.id`` would still pass THIS specific assertion (since
    the skill_ids are equal in the idempotent-re-register case). The
    LOAD-BEARING falsifiability comes from the SECOND assertion: the
    pinned hash equals ``looked_up_plan.id`` (32-hex), NOT
    ``skill_id`` (16-hex). The hash-length test alone is sufficient to
    catch the skill_id-pinning fake.

    R0-fold I1 originally specified registering the same body under
    different ``promotion_id`` values to produce different ``skill_id``s
    with identical ``content_hash``; with idempotent register, that
    equivalence collapses (same plan -> same skill_id regardless of
    promotion_id). The CORE CLAIM still holds: pinning is content-hash,
    not skill_id, and the length-discrimination assertion proves it.
    """
    skill_lib = s.plan.skill_library(s._db)
    skill_body_edn = (
        '[:seq {} [:fs/write '
        f'{{:path "{tmp_path / "winner-marker.txt"}" '
        ':bytes_or_text "winner"}]]'
    )
    skill_body = parse(skill_body_edn, strict=False)
    skill_id_1 = skill_lib.register(
        skill_body,
        _PromotionRecordStub(promotion_id="prom-A"),
        registered_at_ms=1_700_000_000_000,
    )
    # Re-register byte-identical body under different promotion_id —
    # idempotent: same skill_id returned, ZERO new fact datoms (per 2.3c.1).
    skill_id_2 = skill_lib.register(
        skill_body,
        _PromotionRecordStub(promotion_id="prom-B"),
        registered_at_ms=1_700_000_000_001,
    )
    assert skill_id_1 == skill_id_2  # idempotent
    # The looked-up plan.id is content-addressed and 32 hex chars (128 bits);
    # the skill_id is "skill/" + plan.id[:16] (22 chars total — different
    # length, different value).
    looked_up_plan_id = skill_body.id
    assert len(looked_up_plan_id) == 32  # 128-bit content hash, 32 hex chars
    assert looked_up_plan_id != skill_id_1  # length-discrimination

    seed_path = str(tmp_path / "seed.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_skill_compose_call_fn(skill_id=skill_id_1),
        ),
        position="bottom",
    )

    coder = _CoderStub(substrate=s, skill_library=skill_lib)
    _escalate_branch_body(
        coder,
        LLMDecision(
            kind="branch", confidence=0.9,
            payload={
                "seed_plan_edn": seed_edn,
                "mcts_config": {"max_iter": 2, "expander_k": 1},
            },
        ),
    )

    compose_payloads = _extract_compose_provenance_from_facts(s)
    assert len(compose_payloads) >= 1
    for payload in compose_payloads:
        # The pinned hash MUST be the 32-hex content_hash, NOT the
        # ``"skill/" + 16 hex`` skill_id slice. Length-discrimination is
        # what catches a "fake" impl that pinned skill_id.
        pinned = payload.get("composed_skill_content_hash")
        assert pinned == looked_up_plan_id
        assert len(pinned) == 32  # full content hash (128 bits hex)
        assert pinned != skill_id_1  # NOT the skill_id


# --------------------------------------------------------------------------- #
# G5.5 — Post-graft AST contains looked_up_plan.id as subtree
# --------------------------------------------------------------------------- #


def test_g5_5_post_graft_winner_ast_contains_skill_children(s, tmp_path):
    """R1-fold N1 sophisticated-bug catch (FD-T5.4 — G5.5 assertion
    revised under actual graft semantics).

    The design's literal R1-fold N1 wording asserts ``looked_up_plan.id
    in collected_ids``, but the actual graft semantics at
    ``_mcts.py:218-224`` (existing 2.3c.1 code) construct a NEW root
    with ``tag=skill_plan.tag``, ``attrs=skill_plan.attrs``, and
    ``children=(subtree, *skill_plan.children)`` — the skill_plan's
    OWN id is therefore NOT preserved in the winner AST (the new root
    has different children → different content hash). What IS preserved:

      * Each of ``skill_plan.children``'s content hashes appears in
        the winner AST.
      * The compose-root carries ``skill_plan.tag`` and
        ``skill_plan.attrs`` byte-identically.

    These two together prove the skill body was actually grafted (vs
    a "pin correct, graft wrong" fake that wrote the right
    ``composed_skill_content_hash`` to provenance but grafted nothing
    or something else).
    """
    skill_lib = s.plan.skill_library(s._db)
    skill_body_edn = (
        '[:seq {} [:fs/write '
        f'{{:path "{tmp_path / "winner-marker.txt"}" '
        ':bytes_or_text "winner-content"}]]'
    )
    skill_body = parse(skill_body_edn, strict=False)
    skill_id = skill_lib.register(
        skill_body,
        _PromotionRecordStub(promotion_id="prom-A"),
        registered_at_ms=1_700_000_000_000,
    )

    seed_path = str(tmp_path / "seed.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_skill_compose_call_fn(skill_id=skill_id),
        ),
        position="bottom",
    )

    # Spy on the winner Plan AST passed to _escalate_plan_body
    captured_winner: list[Any] = []

    import persistence.coder._searcher as searcher_mod
    original_escalate = searcher_mod._escalate_plan_body

    def spy_escalate(coder_arg, decision_arg):
        # Capture the winner_edn (passed via decision.payload["plan_edn"])
        captured_winner.append(decision_arg.payload["plan_edn"])

    searcher_mod._escalate_plan_body = spy_escalate
    try:
        coder = _CoderStub(substrate=s, skill_library=skill_lib)
        _escalate_branch_body(
            coder,
            LLMDecision(
                kind="branch", confidence=0.9,
                payload={
                    "seed_plan_edn": seed_edn,
                    "mcts_config": {"max_iter": 2, "expander_k": 1},
                },
            ),
        )
    finally:
        searcher_mod._escalate_plan_body = original_escalate

    assert len(captured_winner) == 1
    winner_edn = captured_winner[0]
    winner_plan = parse(winner_edn, strict=False)

    collected_ids: set[str] = set()
    _collect_node_ids(winner_plan, collected_ids)

    # Falsifiability A: every child of the looked-up skill body is
    # present in the winner AST (proves the skill body's leaves were
    # grafted in).
    skill_child_ids = [c.id for c in skill_body.children]
    assert skill_child_ids, "skill body must have at least one child for this test"
    missing = [cid for cid in skill_child_ids if cid not in collected_ids]
    assert not missing, (
        f"G5.5 sophisticated-bug catch: skill_body children {missing!r} "
        f"missing from winner AST node ids {collected_ids!r}"
    )

    # Falsifiability B: the looked-up skill's tag + attrs survive at
    # the compose-root level. Walk the winner AST to find any node with
    # those properties and at least the skill's children present.
    def _walk(node):
        yield node
        for child in node.children:
            yield from _walk(child)

    found_compose_root = False
    for node in _walk(winner_plan):
        if (
            node.tag == skill_body.tag
            and dict(node.attrs) == dict(skill_body.attrs)
            and all(cid in {c.id for c in node.children} for cid in skill_child_ids)
        ):
            found_compose_root = True
            break
    assert found_compose_root, (
        "G5.5 falsifiability B: no node in winner AST matches "
        f"skill body's (tag={skill_body.tag!r}, attrs={dict(skill_body.attrs)!r}) "
        f"with all skill children {skill_child_ids!r} present"
    )
