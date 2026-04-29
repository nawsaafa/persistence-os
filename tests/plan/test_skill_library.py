"""A5 — `SkillLibrary` register / lookup / list_skills.

Content-addressed skill registry backed by the fact store. Tests pin the
public API per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §6 and
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A5.

A5 is decoupled from A7's (yet-to-ship) ``PromotionRecord`` via a
structural ``Protocol`` named ``_PromotionRecordLike``. Tests fabricate
a tiny dataclass with a single ``promotion_id`` field — same shape A7's
real ``PromotionRecord`` will satisfy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import Node, SkillLibrary


# --- Test fixtures -------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _StubPromotionRecord:
    """Minimal stand-in for A7's ``PromotionRecord``.

    SkillLibrary code only reads ``promotion_id``; everything else on
    A7's real dataclass is irrelevant to A5 by design (Protocol-typed).
    """

    promotion_id: str


def _frozen_clock(start_ms: int):
    """Return a clock callable that advances 1 ms per call."""
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _make_db(*, start_ms: int = 1_000_000) -> DB:
    """Fresh in-memory DB with a deterministic clock."""
    return DB(InMemoryStore(), clock=_frozen_clock(start_ms))


def _seq_plan() -> Node:
    """A representative Plan AST with tag ``:seq`` for tag-filter tests."""
    return Node(
        tag=":seq",
        attrs={"name": "demo"},
        children=(
            Node(tag=":llm-call", attrs={"signature": "q -> a"}),
            Node(tag=":llm-call", attrs={"signature": "a -> b"}),
        ),
    )


def _llm_call_plan(suffix: str = "x -> y") -> Node:
    """A leaf ``:llm-call`` plan, useful for collision-avoidance tests."""
    return Node(tag=":llm-call", attrs={"signature": suffix})


# --- Smoke tests on the fixture & API surface ----------------------------- #


def test_skill_library_constructor_takes_db():
    """Public API: ``SkillLibrary(db)`` constructs cleanly."""
    db = _make_db()
    lib = SkillLibrary(db)
    assert lib is not None


def test_skill_id_format_is_namespaced_first_16_hex():
    """``skill_id = "skill/" + plan.id[:16]`` — derivation is deterministic."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    skill_id = lib.register(
        plan,
        _StubPromotionRecord(promotion_id="prom-1"),
        registered_at_ms=1234,
    )
    assert skill_id == "skill/" + plan.id[:16]
    assert len(skill_id) == len("skill/") + 16


# --- Edge case 1 — register → lookup round-trip preserves identity -------- #


def test_register_lookup_round_trip_preserves_plan_and_record():
    """``register → lookup`` returns the same Plan AST + PromotionRecord."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    record = _StubPromotionRecord(promotion_id="prom-abc")

    skill_id = lib.register(plan, record, registered_at_ms=1234)
    looked_up = lib.lookup(skill_id)

    assert looked_up is not None
    plan_back, record_back = looked_up
    # Byte-identity: same Node object (in-memory cache returns the
    # original reference, not a deserialized copy).
    assert plan_back is plan
    assert record_back is record


# --- Edge case 2 — same plan registered twice is idempotent --------------- #


def test_re_registering_same_plan_returns_same_skill_id_no_extra_datoms():
    """Re-registration of the same Plan AST writes ZERO additional datoms."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    record1 = _StubPromotionRecord(promotion_id="prom-1")
    record2 = _StubPromotionRecord(promotion_id="prom-2")  # different record

    skill_id_a = lib.register(plan, record1, registered_at_ms=1234)
    datoms_after_first = list(db.log())

    skill_id_b = lib.register(plan, record2, registered_at_ms=5678)
    datoms_after_second = list(db.log())

    # Idempotent: same skill_id.
    assert skill_id_a == skill_id_b
    # No-op: exactly the same 3 datoms, no fourth/fifth/sixth.
    assert len(datoms_after_first) == 3
    assert len(datoms_after_second) == 3


# --- Edge case 3 — lookup on missing skill_id returns None ---------------- #


def test_lookup_missing_skill_id_returns_none():
    """``lookup`` on an unregistered skill_id is ``None`` (not ``KeyError``)."""
    db = _make_db()
    lib = SkillLibrary(db)
    assert lib.lookup("skill/0123456789abcdef") is None


# --- Edge case 4 — list_skills filters by Plan AST root tag --------------- #


def test_list_skills_with_tag_filter_returns_matching_skills_only():
    """``list_skills(tag_filter=":seq")`` returns only ``:seq``-rooted plans."""
    db = _make_db()
    lib = SkillLibrary(db)
    seq_plan = _seq_plan()
    leaf_plan = _llm_call_plan()

    seq_id = lib.register(
        seq_plan,
        _StubPromotionRecord(promotion_id="prom-seq"),
        registered_at_ms=1000,
    )
    leaf_id = lib.register(
        leaf_plan,
        _StubPromotionRecord(promotion_id="prom-leaf"),
        registered_at_ms=2000,
    )

    seq_only = lib.list_skills(tag_filter=":seq")
    llm_only = lib.list_skills(tag_filter=":llm-call")

    assert seq_only == [seq_id]
    assert llm_only == [leaf_id]


# --- Edge case 5 — list_skills with no filter returns all ----------------- #


def test_list_skills_no_filter_returns_all_registered_skill_ids():
    """``list_skills(tag_filter=None)`` enumerates everything."""
    db = _make_db()
    lib = SkillLibrary(db)
    seq_plan = _seq_plan()
    leaf_plan = _llm_call_plan()

    seq_id = lib.register(
        seq_plan,
        _StubPromotionRecord(promotion_id="prom-seq"),
        registered_at_ms=1000,
    )
    leaf_id = lib.register(
        leaf_plan,
        _StubPromotionRecord(promotion_id="prom-leaf"),
        registered_at_ms=2000,
    )

    all_skills = lib.list_skills()  # tag_filter defaults to None
    assert set(all_skills) == {seq_id, leaf_id}
    assert len(all_skills) == 2


# --- Edge case 6 — reconstruction via db.as_of(t) ------------------------- #


def test_list_skills_reconstructs_from_as_of_snapshot():
    """A SkillLibrary backed by a DB seeded with ``as_of(t1)`` only sees
    skills registered before t1.
    """
    # Frozen clock advances 1 ms per call. Each register() consumes
    # one DB.transact() which calls the clock once.
    db = _make_db(start_ms=10_000)
    lib = SkillLibrary(db)

    plan_a = Node(tag=":seq", attrs={"i": 0})
    plan_b = Node(tag=":seq", attrs={"i": 1})
    plan_c = Node(tag=":seq", attrs={"i": 2})

    sid_a = lib.register(
        plan_a, _StubPromotionRecord(promotion_id="p-a"), registered_at_ms=1
    )  # uses tx_time t = 10_000 ms
    sid_b = lib.register(
        plan_b, _StubPromotionRecord(promotion_id="p-b"), registered_at_ms=2
    )  # uses tx_time t = 10_001 ms
    lib.register(
        plan_c, _StubPromotionRecord(promotion_id="p-c"), registered_at_ms=3
    )  # uses tx_time t = 10_002 ms

    # Build a fresh DB whose store contains only datoms up to t_after_2nd.
    # `as_of(t)` returns a DBView with datoms <= t (tx_time-based).
    t_after_second = datetime.fromtimestamp(10_001 / 1000, tz=timezone.utc) + timedelta(
        microseconds=500
    )
    snapshot = db.as_of(t_after_second)
    assert len(snapshot.datoms) == 6  # 2 skills × 3 datoms each

    snapshot_store = InMemoryStore()
    snapshot_store.append(list(snapshot.datoms))
    snapshot_db = DB(snapshot_store)
    snapshot_lib = SkillLibrary(snapshot_db)

    # No tag filter — pure fact-store enumeration; works without cache.
    visible = snapshot_lib.list_skills()
    assert set(visible) == {sid_a, sid_b}
    assert len(visible) == 2


# --- Edge case 7 — skill_id is deterministic across instances ------------- #


def test_skill_id_is_deterministic_across_skill_library_instances():
    """Two SkillLibrary instances over the same DB derive the same
    skill_id for the same Plan AST."""
    db = _make_db()
    lib_a = SkillLibrary(db)
    lib_b = SkillLibrary(db)

    plan = _seq_plan()
    record = _StubPromotionRecord(promotion_id="prom-1")

    sid_from_a = lib_a.register(plan, record, registered_at_ms=1234)

    # Second instance derives the same id by content-addressing.
    expected_sid = "skill/" + plan.id[:16]
    assert sid_from_a == expected_sid

    # And lib_b can derive the same id without touching its own cache —
    # the skill is in the fact store; lib_b enumerates it via list_skills.
    visible_to_b = lib_b.list_skills()
    assert sid_from_a in visible_to_b


# --- Edge case 7b — cross-instance idempotency (multi-SkillLibrary) ------- #


def test_register_is_idempotent_across_skill_library_instances():
    """Two SkillLibrary instances over the same DB don't double-write.

    Pins the docstring claim that re-registration is "a no-op, full
    stop." The fast-path cache check is in-memory only, so without a
    fact-store probe a second instance over the same store would emit
    3 fresh datoms on the same plan content. That probe lives in
    :meth:`register`; this test is the regression guard for it.
    """
    db = _make_db()
    lib_a = SkillLibrary(db)
    plan = _seq_plan()

    sid_a = lib_a.register(
        plan,
        _StubPromotionRecord(promotion_id="prom-a"),
        registered_at_ms=1234,
    )
    datoms_after_lib_a = list(db.log())
    assert len(datoms_after_lib_a) == 3  # baseline: one registration.

    # A second SkillLibrary over the same DB sees an empty cache but
    # MUST still detect the skill via the fact store.
    lib_b = SkillLibrary(db)
    sid_b = lib_b.register(
        plan,
        _StubPromotionRecord(promotion_id="prom-b-different"),
        registered_at_ms=9999,
    )
    datoms_after_lib_b = list(db.log())

    assert sid_a == sid_b
    assert len(datoms_after_lib_b) == 3  # NO new datoms emitted.

    # And lib_b's cache now contains the plan, so its lookup() can
    # reach back to the freshly-passed plan reference. The promotion-
    # record cache stays unpopulated for this skill in lib_b — the
    # fact store's skill/promotion-record datom points at the
    # originally-registered "prom-a", which lib_b never saw, so
    # lookup() returns None until lib_b also caches "prom-a" via a
    # subsequent register on the same plan with the same record.
    assert lib_b.lookup(sid_b) is None


# --- Edge case 8 — datoms emitted by register have plain-string a-keys ---- #


def test_register_emits_datoms_with_plain_string_a_keys():
    """Regression guard for ``datom.py:114``: ``a`` is plain namespaced
    string with NO leading colon.

    This tests the value as we *construct* the datom — Datom's
    ``__post_init__`` does ``lstrip(":")`` for wire-form normalisation,
    so even if we accidentally passed ``":skill/plan"`` the persisted
    value would canonicalize back to ``"skill/plan"``. We therefore
    assert the post-canonicalization value AND assert the registration
    code path emits exactly the three expected ``a`` values.
    """
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    record = _StubPromotionRecord(promotion_id="prom-1")
    lib.register(plan, record, registered_at_ms=1234)

    a_values = sorted(d.a for d in db.log())
    assert a_values == sorted(
        ["skill/plan", "skill/promotion-record", "skill/registered-at"]
    )
    # No leading colon on any a-key.
    for d in db.log():
        assert not d.a.startswith(":"), (
            f"Datom.a should be plain-string, got leading colon on {d.a!r}"
        )


# --- Edge case 9 — different plans hash to different skill_ids ------------ #


def test_different_plans_yield_different_skill_ids():
    """Collision guard at the 16-hex-char width."""
    db = _make_db()
    lib = SkillLibrary(db)

    sids: set[str] = set()
    # 50 plans differing in a single attr — no collision expected at
    # 16 hex chars (64-bit space, birthday at ~2^32).
    for i in range(50):
        plan = Node(tag=":seq", attrs={"index": i})
        sid = lib.register(
            plan,
            _StubPromotionRecord(promotion_id=f"prom-{i}"),
            registered_at_ms=1000 + i,
        )
        sids.add(sid)

    assert len(sids) == 50


# --- Datom shape verification (3 datoms per registration) ----------------- #


def test_register_emits_exactly_three_datoms():
    """Per design §6: ``skill/plan``, ``skill/promotion-record``,
    ``skill/registered-at``. Three datoms, atomic registration."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    record = _StubPromotionRecord(promotion_id="prom-1")
    lib.register(plan, record, registered_at_ms=1234567)

    datoms = list(db.log())
    assert len(datoms) == 3

    # Map a → datom for shape assertions.
    by_a = {d.a: d for d in datoms}
    expected_skill_id = "skill/" + plan.id[:16]

    assert by_a["skill/plan"].e == expected_skill_id
    assert by_a["skill/plan"].v == plan.id

    assert by_a["skill/promotion-record"].e == expected_skill_id
    assert by_a["skill/promotion-record"].v == "prom-1"

    assert by_a["skill/registered-at"].e == expected_skill_id
    assert by_a["skill/registered-at"].v == 1234567


def test_three_datoms_share_one_tx_id():
    """All three registration datoms share the same ``tx`` — atomic."""
    db = _make_db()
    lib = SkillLibrary(db)
    lib.register(
        _seq_plan(),
        _StubPromotionRecord(promotion_id="prom-1"),
        registered_at_ms=1,
    )
    datoms = list(db.log())
    assert len({d.tx for d in datoms}) == 1


# --- Negative tests on caller-supplied registered_at_ms ------------------- #


def test_register_requires_registered_at_ms_kwarg():
    """``registered_at_ms`` is a keyword-only argument — caller must supply
    it; no implicit ``time.time()`` fallback."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _seq_plan()
    record = _StubPromotionRecord(promotion_id="prom-1")

    # Cannot omit registered_at_ms; cannot pass positionally.
    with pytest.raises(TypeError):
        lib.register(plan, record)  # type: ignore[call-arg]
