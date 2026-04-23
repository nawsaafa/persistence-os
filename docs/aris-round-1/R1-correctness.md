# ARIS Round 1 — Reviewer R1 — Correctness vs. Spec

## Summary grade: 7.4 / 10

Phase 1 is spec-shaped at the *surface* — every module exposes the public
functions and datatypes the spec calls for, the headline invariants
(auto-retraction, Merkle audit chain, aligned-randomness NO-OP) are
enforced and tested, and deviations are mostly documented. The score is
held below 8 by three classes of real defect: (1) **boundary-schema
incoherence** between what `persistence.effect.audit_entry_to_datom`
emits and what `persistence.spec` canonical specs require (slash- vs
colon-prefixed keys, string-typed state keys vs keyword-typed, required
fields audit sets to `None`); (2) **one bitemporal correctness bug** in
`DB.transact` auto-retraction when an assert is issued with an *earlier*
valid-from than its predecessor, producing a companion retract with
`valid_to < valid_from`; (3) **one silent deviation** in the plan-node
canonical spec — the spec §1 describes EDN *vectors*, the registry holds
a *map* shape. None of these are catastrophic, but all three will bite
Phase 2 at exactly the integration points the spec said this layer was
supposed to guarantee.

## Per-module grades

| Module | Grade | Critical issues | Total findings |
|---|---:|---:|---:|
| fact   | 8.0 | 0 | 4 |
| effect | 7.5 | 1 | 5 |
| spec   | 7.0 | 1 | 4 |
| replay | 7.5 | 0 | 4 |

## Findings

### F1 — audit→datom output uses `datom/e` keys; spec requires `:datom/e` [severity: CRITICAL] [effect ↔ spec]

**Location:** `src/persistence/effect/handlers/audit.py:214-240`
(`audit_entry_to_datom`); cross-check with
`src/persistence/spec/_canonical.py:180-209` (`:persistence.fact/datom` registration).

**Spec clause:** `agent1-fact-spec.md` §1 — the datom EDN shape uses
namespaced keywords (`:datom/e`, `:datom/a`, …); `persistence.spec`
registers `:persistence.fact/datom` as a `keys` combinator whose
required keys are exactly `:datom/e`, `:datom/a`, `:datom/v`, etc. The
`CHANGELOG-effect.md` claims: "`audit_entry_to_datom` …
produce the Fact spec §1 8-tuple shape with full round-trip fidelity."

**Observed:** `audit_entry_to_datom` returns a dict whose keys are
`"datom/e"`, `"datom/a"`, `"datom/tx"`, `"datom/tx-time"`, …  — no
leading colon. `conform(":persistence.fact/datom", audit_entry_to_datom(e))`
fails on *every* required key ("missing required key ':datom/e'").

**Gap:** The effect module claims it produces the Fact spec shape, but
the only spec in the project that actually *defines* that shape rejects
its output. This is precisely the silent-deviation class the brief flags
as dangerous — the local test `test_audit_entry_to_datom_has_fact_schema_fields`
checks for `"datom/e"` membership, so the inconsistency is invisible
without running a cross-module conform.

**Fix proposal:**
- `src/persistence/effect/handlers/audit.py:audit_entry_to_datom` — change
  every literal `"datom/..."` key to `":datom/..."`; update
  `datom_to_audit_entry` to match.
- `src/persistence/effect/handlers/audit.py:datom_to_audit_entry` — read
  `a = datom[":datom/a"]`; the `startswith("audit/")` check becomes
  `startswith(":audit/")`.
- Add one test in `tests/effect/test_audit.py` that calls
  `spec.conform(":persistence.fact/datom", audit_entry_to_datom(e))` and
  asserts `result.is_ok`. This is the ARIS-grade boundary check the
  module is missing.

---

### F2 — audit datom fields don't conform to `:persistence.fact/datom` spec even with the key-prefix fix [severity: MAJOR] [effect ↔ spec]

**Location:** `src/persistence/effect/handlers/audit.py:214-260`
vs `src/persistence/spec/_canonical.py:180-209`.

**Spec clause:** The canonical `:persistence.fact/datom` spec requires
`:datom/e` to be a `uuid_()`, `:datom/a` to be a namespaced keyword
(`_KeywordSpec`), `:datom/tx` to be `int_()`, `:datom/tx-time` / `:datom/valid-from`
to be `inst()` (tz-aware datetime), and `:datom/provenance` to contain a
`:source` (keyword) + optional `:signature`.

**Observed (in `audit_entry_to_datom`):**
- `datom/e = entry.run_id or entry.id` — both are strings (either a
  UUID-ish run_id or a `sha256:...` content hash). `run_id` is never
  validated as a UUID; `entry.id` is not a UUID at all (it's the
  content-hash of the entry). Neither conforms to `uuid_()`.
- `datom/a = f"audit/{entry.op}"` — produces e.g. `"audit/llm/call"`,
  without a leading `:`. Fails `_KeywordSpec` regex even after F1 is
  fixed.
- `datom/tx = entry.id` — a *string* (sha256 content hash), not an
  `int_()`. The Fact module's own `Datom.tx` is an `int`. Two meanings
  of `tx` in the same module family.
- `datom/tx-time = entry.recorded_at` — a *float* (epoch seconds from
  `clock/now`), not a `datetime`. Fails `inst()`.
- `datom/provenance[":source"]` is set to the string
  `"persistence.effect.audit"` — but the provenance spec expects a
  *keyword* (`:persistence.effect.audit`). Same for `:signature` etc.

**Gap:** The stated boundary contract ("every audit entry round-trips
into a Fact 8-tuple datom") does not hold under the project's own
canonical spec. Combined with F1, this means the audit→fact integration
point cannot be fed to `DB.transact` as-is, nor validated by `parse(":persistence.fact/datom", ...)`,
nor used by Phase 2's Memory Palace retrofit without an adapter that
doesn't exist yet.

**Fix proposal:**
- In `audit_entry_to_datom`, coerce to the types the canonical spec demands:
  - `datom/e`: generate/require a real UUID on the `AuditEntry.run_id`
    at audit-handler construction (`make_audit_handler` already has
    `run_id` param — validate it's a UUID string).
  - `datom/a`: `":audit/" + entry.op` (prepend colon).
  - `datom/tx`: expose a monotonic integer counter on the audit log
    rather than using `entry.id`; `entry.id` can still serve as
    `:datom/provenance[:signature]`.
  - `datom/tx-time` / `datom/valid-from`:
    `datetime.fromtimestamp(entry.recorded_at, tz=timezone.utc)`.
  - `datom/provenance[":source"]`: `":persistence.effect.audit"`.
- Wire one end-to-end test in `tests/effect/test_audit.py`:
  ```python
  from persistence import spec as S
  d = audit_entry_to_datom(entries[0])
  assert S.conform(":persistence.fact/datom", d).is_ok
  ```
  Gate Phase 2 on this passing.

---

### F3 — DB.transact auto-retraction produces backwards valid interval on out-of-order valid-time asserts [severity: MAJOR] [fact]

**Location:** `src/persistence/fact/db.py:94-113`
(the companion-retract block inside `transact`).

**Spec clause:** `agent1-fact-spec.md` §3 — "New `{:op :assert, :v v2,
:valid-from t2}` for cardinality-one attribute automatically emits a
companion `{:op :retract, :v v1}` at transact time." The closed-interval
construction in paper §4.1 assumes `t2 > prior.valid_from`.

**Observed:** The code unconditionally writes
```python
companion = Datom(valid_from=prior.valid_from, valid_to=vf, op="retract", …)
```
where `vf` is the *new* assert's `valid_from`. There is no check that
`vf >= prior.valid_from`. If a user asserts WACC=0.091 effective
April 19, then later asserts WACC=0.089 effective **April 10** (a
retroactive correction — "the true value was lower from earlier than we
thought"), the companion retract is written with
`valid_from = April 19, valid_to = April 10` — a *negative* interval.
`as_of_valid(April 14)` then excludes the April-19 assert (because
`April 14 < April 19 = valid_from`) and happily returns the new
retroactive assert — which looks correct at first but means the
log now contains a datom that violates `valid_from ≤ valid_to`.

Retroactive corrections are not fringe — they are one of the three
original motivating examples in the spec (§0: "forecasts about next
quarter, corrections about last year").

**Gap:** Silent data-corruption on the *exact* workload the Datomic-of-thought
pitch was designed for. Not called out in the CHANGELOG deviations list.

**Fix proposal:**
- Guard in `src/persistence/fact/db.py:_find_prior_assert` callsite (or
  inside `transact`): if `vf < prior.valid_from`, either:
  1. **Refuse** — raise `BackwardsAssertionError("cannot assert earlier
     valid_from than the prior open interval without an explicit retract")`.
  2. **Close correctly** — emit a companion retract with
     `valid_from = vf, valid_to = prior.valid_from` (spec §1's bitemporal
     rectangle model).
  The spec doesn't dictate which, but silently writing
  `valid_to < valid_from` is not defensible.
- Add tests in `tests/fact/test_db.py`:
  - `test_retroactive_assert_produces_consistent_interval`
  - `test_retroactive_assert_with_valid_from_before_prior_is_rejected` (if choice 1)

---

### F4 — `:persistence.plan/node` spec models maps but the plan spec describes vectors [severity: MAJOR] [spec]

**Location:** `src/persistence/spec/_canonical.py:280-297`
(registration of `:persistence.plan/node`).

**Spec clause:** `agent2-plan-spec.md` §1 — "Every node: `[:node-type
{attrs} & children]`. All nodes carry `:id` (content-addressed sha256),
`:meta {cost latency success-rate}`." Paper §4.3 — "A plan is a labeled
tree where internal nodes are control operators … Every node carries a
content hash `sha256(n)` used as its identity."

**Observed:** The canonical spec registers a `keys` combinator with
required map-keys `:node/id` and `:node/kind` and optional map-keys
`:node/children`, `:node/args`, `:node/docstring`, `:node/meta`.
Example spec-conformant node:
```python
{":node/id": "sha256:abc", ":node/kind": ":seq", ":node/children": [...]}
```
But the plan-spec §1 EDN node is:
```clojure
[:seq {:id "sha256:abc"} child1 child2]
```
— a vector whose first element is the kind, second is an attrs map, rest
are children.

**Gap:** When Module 3 (Plan) lands, the sibling module will either have
to (a) adopt a shape that's a map, contradicting the plan spec §1 and
§8 prototype that uses vectors throughout, or (b) conform plan ASTs
against a spec that doesn't match the module's actual data shape and
get `:persistence.plan/node` conform failures on every real node. This
is a forward-compat spec error embedded in a deferred-module contract
— i.e. exactly the "forward-compat signals that the other modules
should be honoring" the brief asked me to check.

**Fix proposal:**
- Rewrite the `:persistence.plan/node` spec as a fixed-shape vector via
  `tuple_of(_plan_kind, keys(...), seq_of(_any_value))` — matching
  `agent2-plan-spec.md` §1 directly. Preserve the `:node/id` convention
  inside the attrs-map's `:id` key.
- Or: change the plan-spec doc (§1 + §8) to use a map shape, and update
  paper §4.3 to match. Whichever direction, the spec module and the
  source-of-truth doc must agree before Module 3 starts — otherwise
  Phase 2 will rediscover this on day one.
- Same issue applies, to a lesser degree, to `:persistence.plan/skill`
  (§3 of the plan spec uses a map, so that one is fine — the ast slot
  holds the vector node).

---

### F5 — `:persistence.replay/fact` spec requires keyword-prefixed state/obs keys but engine emits bare strings [severity: MAJOR] [replay ↔ spec]

**Location:** `src/persistence/spec/_canonical.py:420-437`
(`:persistence.replay/fact` registration);
`tests/replay/conftest.py:55-72` (toy agent emitting state).

**Spec clause:** `agent4-replay-spec.md` §1 — `:state {:balance 400.0
:position nil :regime :chop}` (EDN keyword keys inside state/obs).
Spec registration encodes this with `map_of(_keyword_spec, _any_value)`.

**Observed:** The test fixture (which *is* the spec §7 prototype in
Python) writes:
```python
state = {"step": 0, "balance": 400.0, "position": None, "pnl": 0.0}
```
Keys are bare strings (`"step"`, `"balance"`) — not `":step"`. A
`Trajectory` serialized from this fixture fails
`conform(":persistence.replay/fact", fact_dict)` because every key in
`:state`/`:obs` rejects `_KeywordSpec`.

**Gap:** The Replay module's own test fixture and demo cannot be
validated against the Replay canonical spec. Either the spec is wrong
or every single Replay consumer is wrong.

**Fix proposal:**
- Prefer: relax `:state`/`:obs`/`:action` value specs to
  `map_of(str_(), _any_value)` — the agent4 prototype uses Python
  dicts with string keys; that's the actual data. Update the EDN wire
  format comment in `agent4-replay-spec.md` §1 to note that the Python
  reference impl uses string keys (keywords on the wire layer only).
- Alternative: rewrite the demo/test fixture to use `":step"` etc.
  Strongly discouraged — it diverges from idiomatic Python agent
  code for cosmetic parity with EDN.

---

### F6 — `:persistence.effect/audit-entry` requires `:audit/policy-id` but audit emits `None` [severity: MAJOR] [effect ↔ spec]

**Location:** `src/persistence/spec/_canonical.py:260`
(`_audit_entry` required keys);
`src/persistence/effect/handlers/audit.py:120-155` (`clause` populating
`policy_id=ctx.get("policy_id")`).

**Spec clause:** Canonical spec registers `:audit/policy-id` as
*required* and typed as `_keyword_spec`. The audit handler factory has
`policy_id: str | None = None` (line 87).

**Observed:** When `make_audit_handler(..., policy_id=None)` is used —
which is the default and is used throughout `test_audit.py` and
`test_composition.py` — every entry has `policy_id=None`. Conforming
that entry to `:persistence.effect/audit-entry` fails:
```
:audit/policy-id: value is None, expected keyword
```

**Gap:** The Effect module's own tests produce audit entries that don't
conform to the Effect module's own canonical spec for audit entries.

**Fix proposal:**
- Make `:audit/policy-id` optional in `_canonical.py`, OR
- Require the caller to pass a `policy_id` (e.g. `":policy/none"`
  sentinel) and refuse to construct otherwise.
- Either choice should be a one-line change plus a spec-round-trip test.

---

### F7 — `EffectHandler` in replay is a stub — integration with `persistence.effect.Runtime` is not wired [severity: MAJOR] [replay]

**Location:** `src/persistence/replay/effect_handler.py` (entire module).

**Spec clause:** `agent3-effect-spec.md` §6 — "Replay engine installs
stack identical to prod except bottom three swapped: `audit → policy →
[replay-intercept] → [recorded-cache] → [clock-replay +
random-replay] → raw-deny`." `agent4-replay-spec.md` §2 — "effect
handlers are the capture mechanism. Every effectful call writes a fact
through a handler. Replay installs a *replay handler* that reads cached
response instead of executing."

**Observed:** `replay.EffectHandler` is a standalone class with its own
`call(op, args, fn)` method. The Effect module's `Runtime` /
`Handler` / `perform(op, **args)` surface is not used — at all — from
Replay. The two modules have *no* shared interface. The test fixture
(`tests/replay/conftest.py`) does not `with_runtime(...)`; it passes a
replay `EffectHandler` instance directly to the toy agent, which calls
`handler.call(...)`. No `perform()` anywhere.

**Gap:** The CHANGELOG-replay.md acknowledges this ("EffectHandler is
the bottom of the replay handler stack … when the Effect module lands,
`record_handler_stack(effect_chain + [replay_handler])` becomes the
standard composition"). That's honest, but it means the "5.4 Replay"
section of the paper describing a replay handler below
`[replay-intercept]` is not operational — a Phase 1 integrator cannot
wire real audit/policy/cache around the replay engine.

This is *documented*, so I judge it **ACCEPT** for R1 correctness — not
a silent deviation. But it caps the score: Phase 1 does not yet prove
the cross-module composition that makes the paper's thesis hold. Phase
2 *must* ship a unifying adapter, and the current
`replay.EffectHandler.call(op, args, fn)` contract is not shaped to
host a handler-stack continuation — `fn` is an opaque thunk, not a
`(args, k, ctx) -> α` clause.

**Fix proposal:**
- Not a Phase-1 fix. Flag this in the Phase 2 plan as the single
  highest-risk integration point.
- Consider shipping a one-page design doc now: `docs/effect-replay-bridge.md`
  that sketches how `replay.EffectHandler` becomes a
  `persistence.effect.Handler` in Phase 2 without breaking the
  trajectory schema.

---

### F8 — `replay._advance_rngs_to_match` hard-codes "one llm draw + one env draw per step" [severity: MINOR] [replay]

**Location:** `src/persistence/replay/engine.py:54-65`.

**Spec clause:** `agent4-replay-spec.md` §2 / §7 prototype — the agent
draws from a single rng per domain; the replay engine "consumes one
draw per domain per step to keep rng aligned during prefix copy."

**Observed:** `_advance_rngs_to_match` calls `rngs["llm"].random()` and
`rngs["env"].random()` exactly once. If a real agent_step takes two
exploration draws (e.g. multi-sample + select best) or uses `rngs["tool"]`
in addition to `rngs["llm"]`/`rngs["env"]`, alignment silently breaks:
prefix facts copy verbatim, but suffix facts re-execute with a
misaligned rng stream.

**Gap:** The CHANGELOG-replay.md notes this constraint. It is a
**documented deviation** (agent contract: one llm + one env draw per
step). Still: the replay engine is hard-wired to that contract with no
way to declare a richer one.

**Fix proposal:**
- Change the signature to `_advance_rngs_to_match(rngs, fact)` (already
  done) and use `fact.random_draws` to know exactly which domains had
  draws and how many. If an agent records `random_draws={"expl": x,
  "env": y, "tool": z}`, advance each corresponding rng once.
- Or: make the agent declare its "draws per step" contract on
  `Trajectory.seeds_schema` and have `_advance_rngs_to_match` consult
  it.
- Either way, a generic replay engine cannot safely assume exactly-one-draw-per-domain
  forever. Ship a fix before Trader v2 wires in — that agent plans to
  take multi-sample exploration draws.

---

### F9 — `:persistence.fact/datom` provenance does not require `:signature`, but paper §4.1 claims ed25519 per-datom [severity: MINOR] [spec]

**Location:** `src/persistence/spec/_canonical.py:166-177`
(`_provenance` keys block).

**Spec clause:** `agent1-fact-spec.md` §1 lists `:signature` in the
example provenance record; paper §4.1 — "A provenance record π
(source, model, prompt-hash, confidence, ed25519 signature) accompanies
each datom." Paper §7.1 concedes per-datom ed25519 is expensive and
suggests batching per-tx.

**Observed:** The `_provenance` spec makes every field — including
`:signature`, `:confidence`, `:source` — *optional*. Registered
datom examples (including the ones `DB.transact` actually writes via
`_hash_fact`) carry `prompt_hash` and whatever the caller passes, but
nothing in the schema nor in the transactor enforces that `:signature`
is populated at commit time.

**Gap:** Not a bug so much as a gap between the paper's claim that
"every datom is signed" and the implementation's willingness to accept
unsigned datoms as spec-conformant. Acceptable for an `alpha1` cut;
should be flagged for Phase 2 when the CHANGELOG already notes
"ed25519 provenance signing — batched at the transaction level per §9"
as deferred.

**Fix proposal:**
- For Phase 2: introduce `:persistence.fact/signed-datom` as a stricter
  variant of `:persistence.fact/datom` that requires
  `:provenance[:signature]`; use the strict variant at the
  regulator-replay boundary (audit chain) and the permissive one for
  day-to-day writes.

---

### F10 — Effect runtime `named_perform` loses masking on the continuation path [severity: MINOR] [effect]

**Location:** `src/persistence/effect/runtime.py:156-197`.

**Spec clause:** `agent3-effect-spec.md` §2 — "Named handlers" + "masked
effects" are *both* Koka-style, meant to compose. The mask set should
apply to dispatch regardless of entry point.

**Observed:** In `named_perform`, the candidates list is built with
```python
for i in range(target_index, -1, -1):
    h = self.handlers[i]
    if i != target_index and h.name in masked:
        continue
```
The target handler itself is *always* included (the `i != target_index`
exception) — which is correct, we want to reach the named target. But
the code then unconditionally skips masked handlers *below* the target.
That's also correct. However the mask set is captured *at entry* via
`self._masked_names()`; nested `with mask(...)` frames inside the
named target body are fine because `perform` re-reads masks on every
dispatch. Subtle but works.

The minor concern: **the well-formedness check (`uncovered_ops`) does
not account for masks.** `rt.is_well_formed(catalog)` could return
`True` for a stack that, when entered under `mask("the-only-audit")`,
has no handler for an audited op. Not a Phase 1 blocker, but it means
the Proposition 2 check is weaker than the paper claims.

**Gap:** Documented? No — `CHANGELOG-effect.md` says "Proposition 2
(well-formedness) check" as if it's complete. It's not; it's static.

**Fix proposal:**
- Rename `Runtime.is_well_formed(catalog)` → `Runtime.is_statically_well_formed(catalog)`
  and add a docstring note about masks. Or add a
  `is_well_formed_under(catalog, masked={...})` overload for the
  dynamic case. One-liner; cosmetic, but keeps the paper honest.

---

### F11 — `fact.demo` re-reads `datetime.now()` after branch to get output right; documented but fragile [severity: MINOR] [fact]

**Location:** `src/persistence/fact/demo.py:76-82`.

**Spec clause:** `agent1-fact-spec.md` §8 prototype uses `_now()` once
at the top and passes it through to `as_of`.

**Observed (documented):** The demo computes `now` once near the top,
uses it for the factual/historical `as_of` calls, then the branch
`as_of` re-reads `datetime.now(timezone.utc)` to include the branch
write's tx_time:
```python
print("Branch:  ", cf.as_of(datetime.now(timezone.utc)).entity("p-042"))
```
The persistence-fact-module memory flags this as a deviation:
"demo re-reads `now()` after branch write (tx_time ordering)."

**Judgment:** ACCEPT — it's documented, necessary because `DB.branch()`
assigns a fresh `tx_time = _now_utc()` to the hypothetical assert and
that can be microseconds *after* the captured `now`. The spec §8
prototype actually has the same issue in Python (it works only because
`datetime.now()` is effectively monotonic across back-to-back calls).
This would bite anyone passing a *stale* `now`, e.g. a test that fixes
a clock.

**Fix proposal:**
- Add a `branch_time()` helper on `DB` that returns the tx_time of the
  last branch write. Or accept a `tx_time: datetime = None` override
  in `branch()` so callers can pin it. Low priority.

---

### F12 — `fact.DBView.entity` tie-breaker (valid_from, tx) is "max wins" — not what "latest assert wins" means for retroactive corrections [severity: MINOR] [fact]

**Location:** `src/persistence/fact/db.py:227-235`.

**Spec clause:** `agent1-fact-spec.md` §3 + paper §5.1 — the projection
rule picks the "latest" assert. The projection adapter here resolves
ties by `(valid_from, tx) > cur`.

**Observed:** Rule is: for the same (e, a), pick the assert with the
greatest `valid_from`; if tied, greatest `tx`. This is correct for
"most recently became true". But for "last thing we learned" (which is
what the demo actually tests — the dfi-agent-rerun on Apr 19 should
win *now*), the right rule is `tx` first, then `valid_from`.

Today it works for the demo because Apr-19-valid-from > Apr-14-valid-from
AND tx2 > tx1, so both rules agree. Consider retroactive correction: an
April 25 assert saying WACC was actually 0.080 effective April 10. By
(valid_from, tx), the April-14 assert (valid_from=Apr 14) beats it
(valid_from=Apr 10). By (tx, valid_from), the April-25 assert wins.
Spec is silent; paper §5.1 says only "latest assert".

**Gap:** Behavior is reasonable but the spec doesn't pin it, and the
choice made here is NOT what "the last thing we learned wins" means to
a regulator.

**Fix proposal:**
- Pin the policy explicitly in `entity()` docstring: "Ties broken by
  greatest valid_from, then greatest tx. Retroactive corrections with
  earlier valid_from do NOT win over older asserts — emit an explicit
  retract to close the prior interval first."
- Consider exposing `entity(policy="most-recently-valid" |
  "most-recently-asserted")` for the regulator case. Defer to Phase 2.

---

### F13 — Policy verdict enum mismatch: spec uses `:allow/:deny/:deny-silently/:require-approval`, handler uses `allow|deny|…` [severity: MINOR] [effect ↔ spec]

**Location:** `src/persistence/spec/_canonical.py:246`
(`_verdict = enum(":allow", …, ":ok", ":error")`);
`src/persistence/effect/handlers/policy.py:61-80` (verdicts compared as
`"allow"` / `"deny"` / `"deny-silently"` / `"require-approval"`).

**Spec clause:** `agent3-effect-spec.md` §4 — policy verdicts as EDN
keywords `:allow | :deny | :deny-silently | :require-approval`.

**Observed:** The policy evaluator (`policy_eval.py:evaluate`) returns
`{"verdict": "allow", ...}` — a bare string. The policy handler's
`clause` compares against bare strings (`if v == "allow": …`). The
canonical `:persistence.effect/audit-entry` spec's `:audit/verdict`
field accepts `(":allow", ":deny", …)` (colon-prefixed) — which means
an audit entry carrying the actual verdict string the handler produces
(e.g. `"ok"` for success, `"error"` on exception) would fail conform.

Combined with F1/F2/F6 this is the *third* boundary-schema inconsistency
between effect and spec.

**Gap:** Spec enum values disagree with runtime values. Conform of any
real audit entry fails on `:audit/verdict`.

**Fix proposal:**
- Pick one: either drop the colons from `_verdict` enum (`enum("allow",
  "deny", …)`) or prepend colons throughout the effect module. I
  recommend dropping colons in the spec: the Python side is the
  ground truth; EDN-style keywords are a documentation choice, not a
  runtime requirement.

---

## Deviations already documented — judgment

| # | Deviation (from CHANGELOG / memory) | Judgment |
|---|---|---|
| D1 | `entity()` uses retract datoms, not `invalidated_by` (fact) | **ACCEPT** — the rationale in `CHANGELOG.md` and the db.py docstring is correct: `invalidated_by` is a tx-time hint, using it as a semantic filter breaks `as_of_valid` for ranges where the superseding assert is outside the view. Well-reasoned. |
| D2 | Demo re-reads `now()` after branch (fact) | **ACCEPT** — see F11. Documented. |
| D3 | `Runtime` is `ContextVar`-scoped, not a module-level `_stack` (effect) | **ACCEPT** — necessary for thread safety and concurrent tests. Paper §4.2 is implementation-agnostic. |
| D4 | `validate_args` is opt-in, not called inside `perform()` (effect) | **CHALLENGE — weak.** The argument ("policy / pii-redact mutate args, auto-validate would reject them") is circular: the catalog's purpose is to declare the *external* contract; handlers that mutate args should canonicalize before re-performing, not dodge validation. Recommend: enforce validation at the `:audit` boundary only. Not a Phase 1 blocker. |
| D5 | Jitter via `:random(kind="jitter")` (effect) | **ACCEPT** — this is the *right* way to make retry bit-reproducible under replay. Spec §8 prototype's inline `random.random()` is actually the worse design. |
| D6 | `replay()` requires explicit `agent_step_fn` + `apply_action_fn` (replay) | **ACCEPT** — earlier draft caused false prompt-hash positives; explicit is better. |
| D7 | Content hash ignores lineage fields (replay) | **ACCEPT** — this is *why* NO-OP replay is byte-identical; it's a load-bearing design choice, not a corner cut. |
| D8 | `ite_per_step` is a stub list of `None`s (replay) | **ACCEPT** — flagged in CHANGELOG, honest. Not needed until Trader v2 cron lands. |
| D9 | `_AnyValueSpec` added, not in brief (spec) | **ACCEPT** — cleaner than `Any`; restricted to EDN-compatible types. |
| D10 | Custom sha256 / version / keyword generators instead of `rstr` (spec) | **ACCEPT** — removes an external dep for the small set of idioms canonical specs use. Reasonable. |
| D11 | Empty-cache replay raises (replay) | **ACCEPT** — cheap alignment against operator error. |
| D12 | Extrapolation switches replay handler to record mode (replay) | **ACCEPT** — matches spec §8 ("mark counterfactuals extending past observation window as `:extrapolated`"). |

## What's correct

1. **`DB.as_of` / `as_of_valid` / `history` / `branch` exactly implement
   paper §4.1.** The bitemporal math is right: transaction-time filter,
   valid-time interval filter, branch seeds a fresh `InMemoryStore`
   from `as_of(t)` so the parent log is untouched. The
   `TestAsOfIdempotence` test and the `test_branch_does_not_mutate_original_db`
   test cleanly prove the two hardest invariants (Proposition 1's
   structural sharing and the idempotence claim from plan.edn verify
   gate) at the API level.

2. **Aligned-randomness NO-OP byte-identity is real.** `trajectory_hash`
   strips lineage fields, `_advance_rngs_to_match` consumes the same
   draws during prefix copy, and `_apply_intervention` consumes them at
   the branch point — together these produce a counterfactual whose
   content hash equals the factual's for a pure state-replacement
   intervention. `test_noop_intervention_produces_byte_identical_trajectory`
   is the load-bearing test and it passes. This is the claim the paper
   §6.2 rests on for CAMO-style fidelity.

3. **Spec module's LLM-friendly error hints have the right shape.**
   `explain_for_llm` produces `Value failed X — Y.` with a leading
   summary and leaf-level `Fix: ...` clauses, plus structured `path`
   breadcrumbs. The format matches the self-healing contract the
   Effect module will want for retry feedback. `test_llm_errors.py`
   gates on the `Fix:` substring.

## Residual risks for Phase 2

1. **audit→fact boundary (F1+F2+F6+F13).** Four independent key-prefix
   and type mismatches mean the canonical spec cannot validate any real
   audit entry. Fix this *before* Memory Palace retrofit — otherwise
   the bitemporal migration will be fed data that fails the module's
   own boundary contract and the regression won't surface until months
   later when a regulator tries to reconstruct a decision.

2. **Retroactive corrections (F3).** The moment BankabilityAI or
   Insurance Comparator receives an assumption correction dated
   earlier than the prior assert, `DB.transact` writes a negative
   interval. This is the spec's *motivating* use case; silent
   corruption is inexcusable here. Phase-2 blocker.

3. **Plan AST shape (F4).** The spec registry is wrong. Whichever
   direction resolves the vector-vs-map conflict, it's a non-trivial
   change to the spec module or the plan spec doc, and the conflict
   must be resolved before Module 3 starts.

4. **Replay↔Effect bridge (F7).** `replay.EffectHandler.call(op, args,
   fn)` is not a `persistence.effect.Handler`. Ship a design doc before
   Trader v2 cron consumes replay output: a thunk-based handler cannot
   host continuations (k), so the current Replay integration contract
   is shaped such that the first real replay-under-production-stack
   will require a redesign of either `EffectHandler.call` or the
   Handler clause signature.

5. **Replay rng contract (F8).** Multi-sample exploration agents
   (Trader v2 future roadmap) will break `_advance_rngs_to_match`'s
   one-draw-per-step assumption silently. Advance rngs from
   `fact.random_draws` instead of hard-coding two domains.
