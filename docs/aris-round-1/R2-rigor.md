# ARIS Round 1 — Reviewer R2 — Test Rigor & Invariants

## Summary grade: 6.4 / 10

The suite is wide (356 tests) and the happy paths are well-exercised, but it does not enforce what the paper claims. Proposition 1 (structural sharing, O(log n) `branch`) is literally untestable because no HAMT exists — `branch()` deep-copies the entire snapshot via `InMemoryStore`. Proposition 2 (well-formedness) has a good unit but no adversarial stack tests (zero handlers, duplicate names, reentrant mask recursion). Proposition 3 (byte-identical NO-OP replay) **is** the sharpest test in the suite, but only for a single-step intervention; multi-step simultaneous interventions, out-of-range step indices, and empty-trajectory replay are untested. The `ContextVar`-scoped runtime claim has zero concurrency tests. The ban on `time.time()` / `datetime.now()` / `random.random()` is aspirational — `fact/db.py:38`, `fact/interceptors/mem0_adapter.py:65,97`, `spec/_primitives.py:13`, and all of `spec/_canonical.py` violate it in production code, and nothing lints for it. Several integrity claims (audit chain deletion, datom content-hash, spec registry silent override) are documented in prose but never asserted by a test. This is round-1 below the gate until the formal propositions are either tested or flagged as deferred.

## Per-module grades
| Module | Grade | Invariant-holes | Edge-case-gaps | Concurrency-gaps |
|---|---:|---:|---:|---:|
| fact   | 6.5 | 4 | 5 | 1 |
| effect | 7.0 | 3 | 3 | 2 |
| spec   | 6.0 | 3 | 2 | 1 |
| replay | 7.0 | 2 | 4 | 2 |

## Findings

### F1 — Proposition 1 (structural sharing / O(log n) branch) is untestable [severity: CRITICAL] [module: fact] [class: INVARIANT]
**Location:** `src/persistence/fact/db.py:174-206`, `src/persistence/fact/store.py:66-107`
**What's missing:** The paper §4.1 Proposition 1 claims `branch(D, t, Δ)` is O(|Δ| log |D|) space and shares non-modified entries via HAMT path copy. The implementation is `InMemoryStore` backed by a plain Python `list[Datom]`. `DB.branch()` does `list(self.as_of(t).datoms)` then `copy.deepcopy(d.provenance)` for every datom — strictly O(n) space, no sharing. There is no HAMT anywhere. No test fails on this gap because no test asserts the complexity or sharing.
**Why it bites:** The abstract, introduction, and §4.1 all cite the HAMT result; reviewers of the paper will ask where it is tested. At production scale (say 10^7 datoms), `db.branch(t, [single_fact])` copies the entire log, eliminating the "counterfactual as a first-class substrate operation" claim behind §4.4's corollary.
**Fix proposal:** Either (a) downgrade Proposition 1 in the paper to "reference implementation uses a flat log; HAMT-backed store is phase-2 work" AND add a `test_branch_is_linear_in_current_implementation` that asserts the O(n) behavior so nobody silently ships a HAMT later without updating the paper; or (b) add a HAMT store and write:
```python
def test_branch_shares_unmodified_nodes_hamt():
    # under HAMTStore
    d0 = DB(HAMTStore()).transact([...1000 facts...])
    cf = d0.branch(t, [single_delta])
    # structural sharing: number of newly-allocated nodes ≤ O(log n)
    assert cf.store.new_nodes_since(d0.store) < 30  # log2(1000) ≈ 10
```

### F2 — Audit chain tampering: deletion and reordering untested [severity: MAJOR] [module: effect] [class: INVARIANT]
**Location:** `tests/effect/test_audit.py:62-72` (only field-mutation is tested)
**What's missing:** `test_tampering_an_entry_breaks_the_chain` mutates `entries[1].args_hash` via `with_fields`. `verify_chain` catches it because the recomputed content hash no longer matches `entry.id`. But the question list asks: what if you DELETE entry 5 of 10? What if you REORDER? No test covers either.
**Why it bites:** A regulator audit is exactly the scenario where an adversary deletes a single embarrassing entry hoping the chain re-seals. `verify_chain` does catch deletion (the next entry's `prev_hash` references a now-missing id), but the production test suite gives no evidence of that — so a future refactor that relaxes the prev_hash check will ship green.
**Fix proposal:** Add to `tests/effect/test_audit.py`:
```python
def test_deletion_of_middle_entry_breaks_chain():
    entries = [... record 10 llm calls ...]
    del entries[4]
    assert verify_chain(entries) is False

def test_reorder_of_entries_breaks_chain():
    entries = [... record 5 calls ...]
    entries[2], entries[3] = entries[3], entries[2]
    assert verify_chain(entries) is False

def test_truncation_from_tail_preserves_chain():
    # design decision: truncation is allowed (shorter but intact)
    entries = [... record 5 ...]
    entries = entries[:3]
    assert verify_chain(entries) is True
```

### F3 — Proposition 3: multi-intervention + out-of-range replay untested [severity: MAJOR] [module: replay] [class: EDGE]
**Location:** `tests/replay/test_determinism.py`, `tests/replay/test_replay.py`, `src/persistence/replay/engine.py:106`
**What's missing:** The no-op byte-identity test is excellent (`test_determinism.py:15`) but exercises a single-step intervention. Three gaps:
1. Two simultaneous interventions (`[{"step":1,...}, {"step":3,...}]`) — `branch_point = min(step)` but seed alignment across both is not proven.
2. `replay()` with `step >= len(traj.facts)` — `_apply_intervention` is never reached (the for-loop terminates before k hits the step), so the intervention is silently dropped. No test catches this.
3. `replay()` of an empty trajectory (`traj.facts == []`) — `_initial_state_of` hits the `if not traj.facts` branch and returns a default; `branch_point = min(step)` still runs; the intervention is silently ignored.
**Why it bites:** A replay with a typo'd step number (e.g. `step=99` when facts has 4) returns a counterfactual whose hash equals the factual's hash (or close to it) without any warning. DPO pipeline silently emits `None` pairs; regression tests silently pass; engineers lose hours chasing ghost bugs.
**Fix proposal:** Add:
```python
def test_replay_with_step_beyond_facts_raises():
    factual = record(...)
    with pytest.raises(ValueError, match="step .* out of range"):
        toy_replay(factual, [{"step": 99, "field": "action", "new_value": {...}}])

def test_replay_of_empty_trajectory_raises():
    t = Trajectory(status="completed", facts=[])
    with pytest.raises(ValueError, match="empty"):
        replay(t, [{"step": 0, "field": "action", "new_value": {...}}],
               agent_step_fn=fn, apply_action_fn=fn)

def test_replay_with_two_simultaneous_interventions_at_different_steps():
    factual = record(...)  # 4 facts
    cf = toy_replay(factual, [
        {"step": 1, "field": "action", "new_value": {"type": "wait"}},
        {"step": 3, "field": "obs",    "new_value": {"price": 999, "regime": "chop"}},
    ])
    assert cf.facts[1].action == {"type": "wait"}
    assert cf.facts[3].obs == {"price": 999, "regime": "chop"}
    # seed alignment: steps 0 and 2 match factual
    assert cf.facts[0].random_draws == factual.facts[0].random_draws
```
Also harden `replay()`: validate `max(step) < len(traj.facts)` at entry.

### F4 — ContextVar runtime: no concurrent-agent bleed-through test [severity: MAJOR] [module: effect] [class: CONCURRENCY]
**Location:** `src/persistence/effect/runtime.py:205-210`, no corresponding test.
**What's missing:** The module docstring says *"No hidden globals across threads: the active runtime lives in a ContextVar, and each Runtime owns its handler list plus its own mask set"* — but nothing exercises two concurrent `with with_runtime(rt)` blocks in different threads / tasks. The `_masks` attribute is a `list[set[str]]` mutated in place — if two `asyncio.Task`s enter `mask("audit")` on the SAME Runtime instance, the mask list corrupts.
**Why it bites:** Production deployment runs the same `Runtime` configuration per-request or per-agent-session. A shared Runtime across two concurrent requests would silently leak mask state — e.g. a masked-audit call in request A unmasks B's audit during the overlap window.
**Fix proposal:**
```python
def test_two_threads_run_independent_runtimes_without_bleed():
    import threading
    captured = {}
    def worker(name, rt):
        with with_runtime(rt):
            captured[name] = perform("llm/call", msg=name)
    rt_a = Runtime([Handler(name="raw", wraps={"llm/call"},
                             clauses={"llm/call": lambda a,k,c: {"who":"A"}})])
    rt_b = Runtime([Handler(name="raw", wraps={"llm/call"},
                             clauses={"llm/call": lambda a,k,c: {"who":"B"}})])
    ta = threading.Thread(target=worker, args=("a", rt_a))
    tb = threading.Thread(target=worker, args=("b", rt_b))
    ta.start(); tb.start(); ta.join(); tb.join()
    assert captured["a"] == {"who": "A"}
    assert captured["b"] == {"who": "B"}

def test_concurrent_mask_on_shared_runtime_is_isolated():
    # Test that mask("audit") in thread A does NOT hide audit in thread B.
    # Will likely FAIL today — Runtime._masks is not ContextVar-scoped.
    # If it fails, the fix is to make _masks a ContextVar(list).
```

### F5 — time.time() / datetime.now() / random.random() ban is aspirational, not linted [severity: MAJOR] [module: all] [class: INVARIANT]
**Location:**
- `src/persistence/fact/db.py:38` — `datetime.now(timezone.utc)` used for every transact's `tx_time`
- `src/persistence/fact/interceptors/mem0_adapter.py:65,97` — `datetime.now(timezone.utc)` as default `valid_from`
- `src/persistence/spec/_primitives.py:13`, `src/persistence/spec/_combinators.py:26` — process-wide `_rng = random.Random()` (unseeded)
- `src/persistence/spec/_canonical.py` — uses bare `random.random()`, `random.randint()`, `random.choice()` throughout the `_generate` methods (lines 62-65, 96-97, 130, 191, 318-319, 350)
- `src/persistence/spec/demo.py:38,39,63,64,92` — demo code uses `dt.datetime.now`

**What's missing:** Paper §4.2 and §4.4 say all non-determinism must route through `:clock/now` and `:random`. In the fact module, `db.transact()` stamps `tx_time` from wall clock, which is fine for the transactor but means the `fact` module itself cannot replay deterministically without the effect runtime's clock handler. In `spec`, the generators use wall-clock randomness, so `generate_example()` called twice produces different values — fine for quickcheck but the quickcheck docstring claims hypothesis shrinking (see F9). Nothing lints or tests this.
**Why it bites:** A replay harness that records a `fact.transact` call can't reproduce its `tx_time` because the value was sampled from the wall clock, not from an intercepted effect. Provenance digests that include `tx_time` (e.g. `_hash_fact`) diverge on replay. In spec: a regression test that pins a generated example breaks on the next run.
**Fix proposal:**
1. Add `tests/test_no_wall_clock_in_production_code.py`:
```python
import ast, pathlib
BANNED = {("datetime","now"), ("time","time"), ("random","random"),
          ("random","randint"), ("random","choice"), ("random","choices")}
ALLOWED_FILES = {"src/persistence/effect/handlers/clock.py",
                 "src/persistence/effect/handlers/raw.py"}  # the one place it's authorized
def test_no_banned_calls_in_src():
    offences = []
    for p in pathlib.Path("src").rglob("*.py"):
        if str(p) in ALLOWED_FILES or "demo" in p.name: continue
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if (node.value.id, node.attr) in BANNED:
                    offences.append(f"{p}:{node.lineno} {node.value.id}.{node.attr}")
    assert not offences, "banned calls:\n" + "\n".join(offences)
```
2. Route `fact/db.py` `_now_utc()` through a pluggable clock so `DB(clock=...)` can seed tx_time deterministically in tests.
3. Seed `spec/_primitives.py::_rng` from an env var or module-level `set_seed()` so generators are reproducible in regression tests.

### F6 — Datom has no content-hash; `_hash_fact` hashes input dict, not canonical datom [severity: MAJOR] [module: fact] [class: INVARIANT]
**Location:** `src/persistence/fact/datom.py`, `src/persistence/fact/db.py:42-47`
**What's missing:** The reviewer brief asks: *"Datom hash equality: does reordering fields produce identical content hash?"* There is no `Datom.content_hash()` method. `_hash_fact(fact: dict)` in `db.py` hashes the pre-tx **input dict** with `sort_keys=True` — so it IS order-insensitive at the input-dict level, but that is not a Datom hash. Two Datoms with identical `(e,a,v,tx,tx_time,vf,vt,op)` but different `provenance` dicts have no defined equality hash.
**Why it bites:** Paper §4.3 says every plan node has `sha256(n)` identity. The Fact module is silent on whether datoms have the same property; downstream code that wants to dedupe datoms by content has no canonical way to do so. A future Kuzu projection that deduplicates on content hash will silently drift if each author implements their own hash.
**Fix proposal:**
```python
# in datom.py
@dataclass(frozen=True, slots=True)
class Datom:
    def content_hash(self) -> str:
        # canonical across field order + provenance order
        from persistence.effect.canonical import canonical_hash
        return canonical_hash({
            "e": self.e, "a": self.a, "v": self.v,
            "tx": self.tx, "tx_time": self.tx_time.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "op": self.op, "provenance": self.provenance,
        })

# in tests/fact/test_datom.py
def test_content_hash_stable_under_provenance_key_reorder():
    a = Datom(..., provenance={"source":"x", "model":"y"})
    b = Datom(..., provenance={"model":"y", "source":"x"})
    assert a.content_hash() == b.content_hash()

def test_content_hash_differs_on_v_change():
    a = Datom(..., v=1)
    b = Datom(..., v=2)
    assert a.content_hash() != b.content_hash()
```

### F7 — Spec registry: silent override on re-register is not tested or warned [severity: MAJOR] [module: spec] [class: INVARIANT]
**Location:** `src/persistence/spec/_registry.py:22-30`
**What's missing:** `register(key, spec)` does `_REGISTRY[key] = spec` with no check for existing bindings. `TestVersionSwap.test_swap_new_spec_old_conformed_still_valid_against_old` celebrates this as a feature. But the reviewer brief asks: *"can you register the same key twice and get silent override?"* — yes, silently. No test asserts this. No warning. No `allow_override=True` kwarg.
**Why it bites:** A test file that registers `:persistence.fact/datom` pollutes the process-global registry for every downstream test. The conftest doesn't reset `_REGISTRY`. Two tests in two files registering different versions of `:persistence.domain/decision` will race based on collection order, creating flaky CI.
**Fix proposal:**
1. Change `register()` to warn on override unless `allow_override=True`:
```python
def register(key: str, spec: Spec, *, allow_override: bool = False) -> None:
    if key in _REGISTRY and not allow_override:
        import warnings
        warnings.warn(f"spec key {key!r} already registered; pass allow_override=True to silence")
    _REGISTRY[key] = spec
```
2. Add `tests/spec/conftest.py` that snapshots and restores `_REGISTRY` around each test.
3. Add a test:
```python
def test_register_twice_warns_unless_override():
    with pytest.warns(UserWarning, match="already registered"):
        S.register(":test.dup/x", S.int_())
        S.register(":test.dup/x", S.str_())
    # explicit override is silent
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        S.register(":test.dup/x", S.float_(), allow_override=True)
```

### F8 — Skill spec missing 4-gate promotion shape [severity: MINOR] [module: spec] [class: COVERAGE]
**Location:** `src/persistence/spec/_canonical.py:358-376` (`_plan_skill`), paper §4.3 para after Prop 2, diagram at line 245
**What's missing:** Paper mentions *"MIPROv2 / MCTS / evo 4-gate promotion"* as the canonical skill-promotion flow. The skill spec has `:skill/stats {:uses :success :cost}` but no gate-tracking fields. Expected shape (per the paper's intent):
- `:skill/promotion-status ∈ {:candidate, :gate-1-trial, :gate-2-dogfood, :gate-3-canary, :gate-4-production, :rejected}`
- `:skill/gate-evidence` — map from gate-id to `{:passed-at :trajectory-ids :metric}`
- `:skill/retract-reason` — non-nil if promotion was reversed
**Why it bites:** Downstream promotion pipeline will need to invent its own ad-hoc fields on skill records; once adopted, migrating is painful. Better to encode the shape at Phase 1 even if the promotion logic is Phase 2.
**Fix proposal:** Extend `_plan_skill` now (the runtime is Phase 2, but the SHAPE should be Phase 1):
```python
_gate_status = enum(":candidate", ":gate-1", ":gate-2", ":gate-3", ":gate-4", ":retracted")
_gate_evidence = keys(required={
    ":gate-id": enum(":gate-1",":gate-2",":gate-3",":gate-4"),
    ":passed-at": inst(),
    ":trajectory-ids": seq_of(uuid_()),
    ":metric": map_of(_keyword_spec, _any_value),
})
# add to _plan_skill.optional:
":skill/promotion-status": _gate_status,
":skill/gate-evidence": seq_of(_gate_evidence),
":skill/retract-reason": maybe(str_()),
```
Plus one test per module that round-trips a skill with full gate evidence.

### F9 — Spec quickcheck docstring claims Hypothesis shrinking; code uses raw `sp.generate()` [severity: MINOR] [module: spec] [class: INVARIANT]
**Location:** `src/persistence/spec/_registry.py:78-107`
**What's missing:** The docstring says *"Uses the hypothesis library for shrinking when available; falls back to the spec's own generate() otherwise."* The code does NEITHER — it just loops `n` times calling `sp.generate()`. `grep -r "from hypothesis" src/persistence/spec` returns zero hits. `tests/spec/test_generative.py` USES hypothesis, but the production `quickcheck()` does not.
**Why it bites:** A failing property test returns unminimized counterexamples; debugging is harder. Worse, the docstring lies, so future readers trust a guarantee that isn't there.
**Fix proposal:**
1. Either implement Hypothesis-backed shrinking:
```python
def quickcheck(key, prop, n=100):
    try:
        from hypothesis import strategies as st, given, settings, Phase
        from hypothesis.errors import UnsatisfiedAssumption
        # Wrap sp.generate in a search strategy
        ...
    except ImportError:
        # fallback
        ...
```
2. OR fix the docstring to match reality and add a test that asserts the docstring is honest:
```python
def test_quickcheck_returns_n_minimal_counterexamples_when_prop_is_always_false():
    # if we claim shrinking, smallest failing int should be the first failure
    S.register(":test.shrink/int", S.int_())
    failures = S.quickcheck(":test.shrink/int", lambda v: v > 1000, n=10)
    # with shrinking: first failure should be the min int
    # without: probably any int. Current impl: NO shrinking.
    assert all(isinstance(f, int) for f in failures)
```

### F10 — Edge: empty log, single datom, branch at t < first tx, history(unknown) [severity: MINOR] [module: fact] [class: EDGE]
**Location:** `tests/fact/test_db.py`
**What's missing:** The reviewer brief asks for:
- Empty datom log: covered by `tests/fact/test_store.py:35` but not at DB level (what does `DB().as_of(now).entity("x")` return?)
- Single-datom log: implicit but no dedicated test
- Transacting at the same `tx_time` (ordering): not tested. `_tx_counter` gives monotonic tx ids but `tx_time` is sampled per transact; two transacts in the same microsecond would share tx_time. `as_of(shared_tx_time)` would include both — is that desired?
- Retracting a fact that doesn't exist: not tested. `db.transact([{"op":"retract", ...}])` on an entity that was never asserted currently just appends a retract datom (no error, no warning). `entity()` projection excludes the (non-existent) assert which is a no-op.
- `branch` at tx_time before the first datom: `db.as_of(1999-01-01)` returns empty view; `branch` then writes into a fresh store. No test covers this — but `test_as_of_filters_on_tx_time` DOES test cold_t and passes, so this is mostly a documentation gap.
- `history(e)` for unknown entity: covered by `test_history_is_empty_for_unknown_entity:170`. OK.

**Why it bites:** Silent-success retract-of-nothing is the kind of bug that produces "ghost" retracts in the log — auditors see 20 retracts, find no matching asserts. Same-tx_time rows corrupt the `since(t)` incremental-sync path.
**Fix proposal:**
```python
def test_db_empty_log_entity_returns_empty_dict(store):
    db = DB(store)
    assert db.as_of(datetime.now(timezone.utc)).entity("anything") == {}

def test_retract_of_nonexistent_fact_either_errors_or_warns(store):
    db = DB(store)
    # design decision needed: error? warn? silent?
    # current: silent. document the choice with a test that pins it:
    db2 = db.transact([
        {"e":"ghost","a":"x","v":1,"valid_from":_dt(2026,1,1),"op":"retract"}
    ], provenance={})
    log = list(db2.log())
    assert len(log) == 1
    assert log[0].op == "retract"
    # if design says "error": change to pytest.raises(...)

def test_two_transacts_at_identical_tx_time_are_distinguishable_by_tx_id(store):
    fixed_time = _dt(2026, 1, 1)
    # force both transacts to use the same tx_time by monkey-patching _now_utc
    import persistence.fact.db as db_mod
    orig = db_mod._now_utc
    db_mod._now_utc = lambda: fixed_time
    try:
        db = DB(store).transact([{"e":"a","a":"x","v":1,"valid_from":fixed_time}], provenance={})
        db = db.transact([{"e":"b","a":"x","v":2,"valid_from":fixed_time}], provenance={})
    finally:
        db_mod._now_utc = orig
    txs = [d.tx for d in db.log()]
    assert len(set(txs)) == 2  # tx ids still distinct
    # but since(fixed_time) strict-greater returns empty; since(fixed_time - 1us) returns both
    assert list(db.since(fixed_time)) == []
```

### F11 — Entity projection uses retract-semantics; no test that old `invalidated_by` semantics agree on non-edge cases [severity: MINOR] [module: fact] [class: INVARIANT]
**Location:** `src/persistence/fact/db.py:219-253` (the docstring explicitly says *"invalidated_by is NOT consulted here. [...] respecting it would break as_of_valid for ranges in which the superseding assert is outside the view."*)
**What's missing:** The reviewer brief asks: *"Are the deviations' justifications tested? (e.g., fact `entity()` uses retract datoms; test that this matches the old `invalidated_by` behavior for non-edge cases.)"* The deviation is documented but not anchored in a test — a future engineer could re-introduce `invalidated_by` filtering in `entity()` and break `as_of_valid` without noticing.
**Why it bites:** Behavioral regression: if someone "optimizes" `entity()` to skip `invalidated_by` datoms, queries like "what was the WACC as known on Apr 15" break because the auto-retract companion is filtered out AND the old assert is filtered out, returning `{}`.
**Fix proposal:**
```python
def test_entity_projection_does_not_use_invalidated_by_hint(store):
    """Regression: entity() MUST read retract datoms, not the invalidated_by
    pointer, because the superseding assert may lie outside the view.
    """
    db = DB(store)
    db = db.transact([{"e":"p","a":"w","v":0.087,"valid_from":_dt(2026,4,14)}], provenance={})
    db = db.transact([{"e":"p","a":"w","v":0.091,"valid_from":_dt(2026,4,19)}], provenance={})
    # Apr 15 view: later assert (Apr 19) is outside the valid-time range.
    # Old assert has invalidated_by set. If entity() consulted invalidated_by,
    # it would return {} instead of {"w": 0.087}.
    entity = db.as_of_valid(_dt(2026,4,15)).entity("p")
    assert entity == {"w": 0.087}, "entity() leaked invalidated_by semantics"
```

### F12 — Handler stack: zero handlers, duplicate names, recursive mask untested [severity: MINOR] [module: effect] [class: EDGE]
**Location:** `tests/effect/test_runtime.py`
**What's missing:**
1. Zero-handler stack: `perform("llm/call")` on `Runtime([])` — should raise `Unhandled` clearly, but there's no test. `test_named_raises_when_handler_missing` only covers `named()`, not `perform()`.
2. Duplicate handler names: `Runtime([Handler(name="audit", ...), Handler(name="audit", ...)])`. What does `named("audit", ...)` dispatch to? Current code returns the first-matching, but nothing tests it.
3. Recursive `mask()`: `with mask("audit"): with mask("audit"):` — both push the same name; on exit the stack pops correctly. Is this idempotent? Not tested.
4. `mask()` of a name that doesn't exist — silently works. Should it warn?
**Why it bites:** Silent behavior in edge cases is the #1 source of production-vs-test skew for effect systems. The Koka-style semantics the paper cites are precisely about these corner cases.
**Fix proposal:**
```python
def test_perform_on_empty_runtime_raises_unhandled():
    with with_runtime(Runtime([])):
        with pytest.raises(Unhandled, match="no handler covers"):
            perform("llm/call", model="x")

def test_duplicate_handler_names_named_dispatches_to_first_or_errors():
    h1 = Handler(name="audit", wraps={"x"}, clauses={"x": lambda a,k,c: 1})
    h2 = Handler(name="audit", wraps={"x"}, clauses={"x": lambda a,k,c: 2})
    rt = Runtime([h1, h2])
    # DESIGN: which does named("audit", "x") hit? Document with a test.
    # Either error, or pick the outermost (last in list).
    with with_runtime(rt):
        result = named("audit", "x")
    # current impl picks first-index, which is h1 (innermost) → 1
    assert result == 1

def test_mask_of_same_name_twice_is_idempotent():
    calls = []
    h = Handler(name="audit", wraps={"x"}, clauses={"x": lambda a,k,c: calls.append(1)})
    raw = Handler(name="raw", wraps={"x"}, clauses={"x": lambda a,k,c: 0})
    rt = Runtime([raw, h])
    with with_runtime(rt):
        with mask("audit"):
            with mask("audit"):
                perform("x")
    assert calls == []

def test_mask_of_unknown_name_is_silent_noop():
    # doc behavior pin; change to pytest.warns if design says otherwise
    raw = Handler(name="raw", wraps={"x"}, clauses={"x": lambda a,k,c: "ok"})
    rt = Runtime([raw])
    with with_runtime(rt), mask("no-such-handler"):
        assert perform("x") == "ok"
```

### F13 — Spec `conform` on `None`, nested invalid: not covered [severity: MINOR] [module: spec] [class: EDGE]
**Location:** `tests/spec/test_primitives.py`, `test_combinators.py`
**What's missing:** The reviewer brief asks for: `conform` on `None`, wrong type, nested invalid.
- `S.int_().conform(None)` — returns ConformError. Untested explicitly.
- `S.keys(required={":a": S.keys(required={":b": S.int_()})}).conform({":a": {":b": "bad"}})` — nested error path. The current tests check `TestKeys.test_error_path_for_nested_failure` with one level; two levels deep is not tested.
- `S.seq_of(S.int_()).conform(None)` — untested.
**Why it bites:** LLM outputs often contain `null` values and deeply-nested structures. A missing test for `None` means a silent bug that accepts None anywhere could ship.
**Fix proposal:**
```python
def test_int_spec_rejects_none():
    assert not S.int_().conform(None).is_ok

def test_keys_spec_rejects_none_value_for_required_field():
    sp = S.keys(required={":a": S.int_()})
    assert not sp.conform({":a": None}).is_ok
    assert not sp.conform(None).is_ok  # None is not a dict

def test_deeply_nested_error_reports_path():
    sp = S.keys(required={":a": S.keys(required={":b": S.seq_of(S.int_())})})
    err = sp.conform({":a": {":b": [1, 2, "bad", 4]}})
    assert not err.is_ok
    # path should include :a, :b, and index 2
    all_paths = [e.path for e in err.sub_errors]
    joined = str(all_paths)
    assert ":a" in joined and ":b" in joined and "2" in joined
```

### F14 — Policy evaluator on malformed policy data [severity: MINOR] [module: effect] [class: EDGE]
**Location:** `tests/effect/test_policy_eval.py`
**What's missing:** `test_unknown_operator_raises_policy_error` covers one malformed case. What about:
- Empty `:when` clause: `{"when": [], ...}` — current behavior is undefined.
- `:when` that's a string, not a list: `{"when": ":op=", ...}` — current code would fail with IndexError.
- Rule without `"on-fail"`: undefined.
- Rule without `"id"`: reasons array behavior undefined.
- Policy without `"policy/id"`: no test.
**Why it bites:** Policies come from YAML/EDN files authored by compliance officers, not engineers. Typos and malformed rules are inevitable. An IndexError leaks the stack trace to production logs; a `PolicyError` is the contract.
**Fix proposal:**
```python
def test_policy_with_empty_when_clause_raises():
    policy = {"policy/id":"p","rules":[{"id":"r","when":[],"on-fail":"deny"}]}
    with pytest.raises(PolicyError): evaluate(policy, PRINCIPAL, "x", {}, mode="live")

def test_policy_rule_without_id_still_evaluates_but_reason_is_anonymous():
    policy = {"policy/id":"p","rules":[{"when":[":op=","x"],"on-fail":"deny"}]}
    v = evaluate(policy, PRINCIPAL, "x", {}, mode="live")
    assert v["verdict"] == "deny"
    # reason should not raise KeyError; it may say "<anonymous>"

def test_policy_rule_without_on_fail_raises():
    policy = {"policy/id":"p","rules":[{"id":"r","when":[":op=","x"]}]}
    with pytest.raises(PolicyError, match="on-fail"):
        evaluate(policy, PRINCIPAL, "x", {}, mode="live")
```

### F15 — Trajectory hash: no test that re-serialization produces same hash [severity: MINOR] [module: replay] [class: INVARIANT]
**Location:** `tests/replay/test_trajectory.py`
**What's missing:** `test_trajectory_round_trip_through_json` tests value equality after JSON round-trip but NOT hash equality. The reviewer brief asks: *"Trajectory hash: does re-serializing produce the same hash? (JSON ordering)"* — untested.
**Why it bites:** If `trajectory_hash` depends on dict iteration order rather than canonicalization, two instances of the same trajectory (one deserialized from JSON) might hash differently. That breaks DPO dedup and regression-test identity.
**Fix proposal:**
```python
def test_trajectory_hash_stable_across_json_roundtrip():
    t = Trajectory(facts=[_mk_fact(0),_mk_fact(1)], outcome={"pnl":3.0}, seeds={"llm":42,"tool":0,"env":0})
    t.hash = trajectory_hash(t)
    payload = t.to_json()
    restored = Trajectory.from_json(payload)
    assert trajectory_hash(restored) == t.hash

def test_trajectory_hash_ignores_id_field():
    # Already covered by test_trajectory_hash_is_content_addressed. Good.
    pass

def test_trajectory_hash_ignores_dict_key_order_in_state():
    t1 = Trajectory(facts=[Fact(step=0, t=0,
                                 state={"a":1,"b":2},
                                 obs={}, llm_in={}, llm_out={}, action={}, tool_calls=[], random_draws={})])
    t2 = Trajectory(facts=[Fact(step=0, t=0,
                                 state={"b":2,"a":1},
                                 obs={}, llm_in={}, llm_out={}, action={}, tool_calls=[], random_draws={})])
    assert trajectory_hash(t1) == trajectory_hash(t2)
```

### F16 — CLI/demos: smoke-run only, not validated against spec §8 values beyond string-match [severity: MINOR] [module: all] [class: COVERAGE]
**Location:** `tests/fact/test_demo.py`, `tests/replay/test_demo.py`
**What's missing:** The demo tests assert string presence (`"0.091" in out`, `"pnl_delta" in out`). They don't parse the output or check numerical accuracy beyond one decimal. A subtle rounding regression (0.0910001 → "0.091001") would still pass the string check but fail a parse-and-assert-close test.
**Why it bites:** Demo drift — the demo is the external contract with users but the test is pure smoke. A silent regression in `as_of_valid` that returns the wrong float but still formats to `"0.091"` would ship.
**Fix proposal:**
```python
def test_fact_demo_numerics_match_spec_exactly():
    out = subprocess.run(...).stdout
    # Parse demo output with a regex and assert the floats with pytest.approx
    import re
    now_match = re.search(r"Now:\s+\{.*'project/wacc':\s*([\d.]+)", out)
    assert now_match
    assert float(now_match.group(1)) == pytest.approx(0.091, abs=1e-6)
```

## Paper propositions — coverage audit

**Proposition 1 (Structural sharing, O(|Δ| log |D|) branch).** NOT tested. Not testable with current implementation — no HAMT exists. Tests present (`TestBranch` in `test_db.py:186-231`) only check functional isolation, not complexity. Flag: F1 CRITICAL.

**Proposition 2 (Handler-stack well-formedness).** Tested at `tests/effect/test_runtime.py:84-108` — positive and negative cases for `is_well_formed` and `uncovered_ops`. Solid. But: (a) no test for well-formedness of an empty stack against an empty catalog (tautological but worth pinning), (b) no test that `is_well_formed` considers `mask` state — a stack "well-formed" statically can become ill-formed when all handlers for an op are masked at runtime. Flag: partial MAJOR.

**Proposition 3 (Replay determinism with NO-OP intervention → byte-identical).** Tested at `tests/replay/test_determinism.py:18-49` — this is the best test in the suite. `test_noop_intervention_produces_byte_identical_trajectory` checks per-field equality on every Fact AND `trajectory_hash` equality. Solid. Gaps: single-step NO-OP only; multi-step NO-OP, NO-OP at step 0, NO-OP at last step untested. Flag: F3 MAJOR.

**Proposition (implicit) 4 (Skill 4-gate promotion shape).** NOT anticipated in spec. The `:persistence.plan/skill` spec has usage stats but not gate-tracking fields. Flag: F8 MINOR.

## Lint-worthy items

1. **`time.time()` / `datetime.now()` / `random.random()` ban** — should be a static lint against `src/` (excluding `effect/handlers/clock.py`, `effect/handlers/raw.py`, and `demo.py` files). Multiple violations currently in `fact/db.py`, `fact/interceptors/mem0_adapter.py`, `spec/_primitives.py`, `spec/_combinators.py`, `spec/_canonical.py`. See F5.
2. **Spec registry re-registration** — should warn by default; `allow_override=True` opt-in. See F7.
3. **Handler `wraps` vs `clauses`** — currently `__post_init__` checks `clauses.keys() <= wraps` but not the reverse (wraps could declare ops with no clause). Add: `if set(self.wraps) != set(self.clauses): warn`.
4. **Trajectory hash canonicalization** — enforce `canonical_dumps` is used for trajectory_hash. Currently not verified by a test (F15).
5. **Datom timestamps** — `Datom.__post_init__` correctly rejects naive datetimes. This IS linted at construction time. 
6. **Policy rule shape** — when loading a policy document, validate with `:persistence.effect/audit-entry`-style spec. Currently the evaluator crashes on malformed rules (F14).

## What's rigorous

1. **`test_noop_intervention_produces_byte_identical_trajectory` (`test_determinism.py:18`).** Per-field equality on every Fact, plus hash equality. This is the right shape for Proposition 3.
2. **Audit handler `test_tampering_an_entry_breaks_the_chain` (`test_audit.py:62`).** Explicit integrity assertion — recomputes the chain after field mutation. Extend with deletion + reorder cases (F2) and this becomes the best test in the effect module.
3. **Spec `TestNoSilentCoercion` (`test_primitives.py:98-113`).** Disciplined — pins the "parse don't validate" contract. `test_int_spec_rejects_bool_even_though_python_subclasses` is exactly the kind of corner the paper implicitly depends on.
4. **Replay `test_replay_mode_refuses_net_fetch_on_cache_miss` (`test_effect_handler.py:56-66`).** The safety contract — replay refuses to hit real APIs. Correct and sharp.
5. **Backend parity `test_inmemory_and_sqlite_agree` (`test_db.py:293`).** Cross-backend equivalence under one scenario. Good pattern; should be expanded to the full `test_db.py` matrix via parametrization.

## Residual risks for Phase 2

1. **Structural sharing debt.** When a user calls `branch` on a 10M-datom store, it deep-copies everything. First real workload will crash on RAM or block for tens of seconds. Either ship a HAMT store in Phase 2 OR weaken the paper's Proposition 1 language. Non-negotiable before production.

2. **ContextVar concurrency.** Under FastAPI + async, a shared `Runtime` instance across requests will leak mask state through the mutable `_masks` list. First multi-tenant load test will find this. Fix: make `_masks` a `ContextVar(list)`, not a plain list.

3. **Spec registry pollution in long-running processes.** The process-wide `_REGISTRY` has no reset path. A long-lived server that reloads user-defined specs will accumulate them indefinitely. `unregister(key)` missing.

4. **Wall-clock leakage.** `fact/db.py:38` and `mem0_adapter.py:65,97` sample wall clock directly. Any replay that involves `DB.transact` cannot produce byte-identical `tx_time`. In Phase 2, when the replay module tries to replay a trajectory that included fact transactions, this will diverge silently.

5. **No deletion/reorder tests on audit chain.** Regulator audit will test exactly this adversarial case. Add the test before shipping to the insurance comparator deployment.

6. **Datom content-hash missing.** First downstream dedup consumer (Kuzu projection) will invent its own hash. Two weeks later, two projections drift. Define `Datom.content_hash()` now.

7. **Demo tests are smoke-only.** The `persistence.fact.demo` output is the external-facing contract for users first learning the library. A string-match test admits numerical regressions. Parse and assert-approx before first external demo.

8. **Skill spec does not anticipate 4-gate promotion.** Phase 2 will need to add fields to `:persistence.plan/skill`. If you add them now (F8), Phase 2 just populates the fields instead of migrating schemas.
