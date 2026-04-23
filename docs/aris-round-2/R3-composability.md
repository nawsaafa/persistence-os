# ARIS Round 2 — Reviewer R3 — Composability & Phase-2 Integration

**Commit:** `a28d8f5` on `main` · **Repo:** `/Users/nawfalsaadi/Projects/persistence-os/`
**Round 1 grade (predecessor):** 4.5 / 10 (the lowest; gate 7.0).
**Round 2 grade:** **8.6 / 10** — gate ≥ 8.5 met. Biggest ladder jump of any reviewer by design.
**Test count:** 356 baseline → 463 (+107) — all green in 1.99s.

## Summary

The R1 call was that Phase 1 was "four good prototypes that have not been tested as a stack." That
has been fixed in the spine: datom/trajectory shapes are now reconciled by spec-conforming
adapters (`fact/wire.py`, `audit.audit_entry_to_datom`, `Trajectory.to_edn`); the replay ↔ effect
boundary is a real `make_replay_handler(...) -> effect.Handler` sitting on a `Runtime` stack with
a load-bearing record→replay→hash-equal e2e test; the op namespace is leading-colon everywhere
in live source; `effect.__all__` exposes 33 public names; `pyproject.toml` ships the dev deps
that were silently missing; and the `_tx_counter` module global is gone. Each of R3 F1/F2/F3/F5/F6/F7/F8/F9/F10
is either FIXED or PARTIAL-but-defensible; R3 F4 is (explicitly) DEFERRED.

Residual composability debt is real but narrower: three new defects identified
(§2 below) around (a) `Store.next_tx` TOCTOU under concurrency, (b) `wire.datom_to_wire`
identity loss on already-colon-prefixed `a` / source-already-keyworded provenance, and
(c) `audit_entry_to_datom` not self-conforming inside the function (the conform contract lives only
in `fact/wire.py`). None block Round 3 lift-off.

## 1. R1 finding remediation table

| # | Finding | Status | Evidence | Grade |
|---|---|---|---|---:|
| R3 F1 | Three datom schemas (fact / effect / spec) never round-tripped | **FIXED** | New `src/persistence/fact/wire.py` (`datom_to_wire` / `wire_to_datom`, both call `_S.parse(":persistence.fact/datom", ...)` at line 115 and 128). `audit_entry_to_datom` rewritten (`src/persistence/effect/handlers/audit.py:270-340`) to emit spec-conformant EDN-keyword wire shape. Tests: `tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec` (passes; `S.conform(":persistence.fact/datom", datom).is_ok`), `tests/fact/test_wire.py::TestDatomToWire::test_wire_conforms_to_fact_spec` (passes), `tests/effect/test_audit.py::test_datom_roundtrip_preserves_audit_entry` (passes). Audit→Fact path under `effect.Runtime` now produces a dict whose spec-conform is the literal test assertion. | 9 |
| R3 F2 | `replay.EffectHandler` unpluggable under `effect.Runtime` | **FIXED** | `src/persistence/replay/effect_handler.py:220-316` `make_replay_handler(mode, wraps, cache, calls) -> effect.Handler` produces a real `Handler(name, wraps, clauses, ctx)` with the `(args, k, ctx)` clause signature (line 291 `def clause(args, k, ctx)`). Delegates record mode via `k(args)` (line 296); replay mode returns cached without calling `k`. `tests/integration/test_effect_replay_bridge.py::test_record_then_replay_byte_identical_trajectory` (passes) runs a tiny agent under `effect.perform()` on a real `Runtime`, records through the bridge, replays on a fresh `Runtime([replay_handler])` with NO raw handlers, asserts `trajectory_hash(replayed) == trajectory_hash(recorded)`. `NON_REPLAYABLE_OPS` is now `{":net/fetch", ":tool/call"}` matching catalog keys. No mocks in the test. | 9.5 |
| R3 F3 | `Trajectory.to_dict` fails `:persistence.replay/trajectory` spec | **FIXED** | `src/persistence/replay/trajectory.py:202-257` — new `Trajectory.to_edn()` emits EDN-keyword keys, tz-aware `:trajectory/started-at` (defaults to epoch UTC when unset — line 228), keyword-prefixed `:trajectory/status` / `:trajectory/wall-clock-basis` / seeds / tags, default `:trajectory/hash="sha256:unset"` sentinel (line 233). `tests/replay/test_trajectory.py::test_to_edn_conforms_to_replay_trajectory_spec` (passes; `S.conform(":persistence.replay/trajectory", edn).is_ok`), plus `test_from_edn_roundtrips_through_to_edn` (passes). `to_dict` is retained for internal JSON round-trip — correct design: `to_dict` and `to_edn` serve different use cases (JSON persistence vs spec boundary). Minor nit: `to_edn` does not self-conform inside the function, so a caller can still produce a non-conforming dict if upstream violates invariants — but the spec check happens in the downstream caller per the fact/wire.py pattern. | 8 |
| R3 F4 | SQLite `AUTOINCREMENT` breaks Postgres portability | **DEFERRED** | `src/persistence/fact/migrations/0001_datom_log.sql:20` still reads `seq INTEGER PRIMARY KEY AUTOINCREMENT`; line 3 still claims "Portable between Postgres 14+ and SQLite 3.37+" — the claim is false and remains. Explicitly flagged as deferred in W-integration summary ("R3 F4 — SQLite AUTOINCREMENT Postgres portability"). No Postgres testcontainer in CI. This is the one unresolved R3 finding; the portability claim in the SQL header comment is an honesty defect that should be struck even without the fix. | 4 |
| R3 F5 | `Mem0Interceptor.add` passed unknown kwargs to `mem0.Memory` | **FIXED** | `src/persistence/fact/interceptors/mem0_adapter.py:140-198` — `add(*, e, a, v, valid_from=None, provenance=None, **mem0_kwargs)` — datom fields are stamped into `metadata=` dict (line 179 `_merge_metadata(...)`); real call is `self.mem0.add(messages, metadata=merged_metadata, **mem0_kwargs)` (line 194). No `e=`, `a=`, `v=`, `valid_from=` forwarded. Same pattern for `update` (line 247). Test fakes are now strict: `tests/fact/test_interceptor.py::FakeMem0.add` (line 48-59) mirrors real mem0ai 2.x signature exactly — no `**kw` permissive swallow. Additional `tests/integration/test_mem0_signature.py::StrictFakeMem0` introspects `inspect.signature(mem0.Memory.add)` of the installed `mem0ai` package (integration-marked, passes against `.venv/` install). Real-world TypeError is reproducible. | 9.5 |
| R3 F6 | Three verdict vocabularies disagree | **PARTIAL** | New `src/persistence/effect/verdicts.py` (93 lines) defines `PYTHON_VERDICTS`, `EDN_VERDICTS`, `as_edn`, `as_python`; 32 parametrised tests in `tests/effect/test_verdicts.py` cover round-trip and idempotency. `audit_entry_to_datom` calls `_verdict_as_edn` at the wire boundary (`src/persistence/effect/handlers/audit.py:31,323`); `datom_to_audit_entry` calls `_verdict_as_python` (line 365). **But**: `src/persistence/effect/policy_eval.py:185-189` still writes bare string literals `"deny"` / `"allow"` — it does not `from persistence.effect.verdicts import PYTHON_VERDICTS` and check `verdict in PYTHON_VERDICTS`. If a policy rule's `on-fail` is set to a novel string, `policy_eval` passes it through unchecked; `audit_entry_to_datom` then raises `ValueError` from `as_edn` at the boundary (loud, not silent — salvageable). Defensible but not fully unified: the reconciler is the source of truth *at the wire boundary only*. | 7 |
| R3 F7 | `effect/__init__.py __all__ = []` | **FIXED** | `src/persistence/effect/__init__.py:64-113` — `__all__` now lists 33 names including `perform`, `Runtime`, `Handler`, `Unhandled`, `mask`, `named`, `with_runtime`, `AuditEntry`, `audit_entry_to_datom`, `make_audit_handler`, `verify_chain`, all handler factories, the verdict reconciler exports. Verified: `PYTHONPATH=src python -c "from persistence.effect import perform, Runtime, make_audit_handler, Handler, Unhandled, mask, named, with_runtime, make_policy_handler, make_cache_handler, make_retry_handler, verify_chain, audit_entry_to_datom, AuditEntry"` → no error. `tests/effect/test_public_surface.py` (9 tests, all pass). | 10 |
| R3 F8 | `spec.conform` has zero callers outside `src/persistence/spec/` | **PARTIAL** | `fact/wire.py:115,128` — `datom_to_wire` and `wire_to_datom` both call `_S.parse(":persistence.fact/datom", ...)` on every invocation. That's the one real cross-module boundary that exercises the registered spec, and it's the right one (audit→datom→DB and DB→wire→any-serialization). **But**: (a) `audit_entry_to_datom` does NOT self-conform its output (the spec check happens only if a downstream `wire_to_datom` / test calls parse); (b) `Trajectory.to_edn` does NOT self-conform; (c) `DB.transact` does NOT conform input facts. So the "parse-don't-validate discipline at every cross-module boundary" mandate from R1 is only partially executed: fact↔wire is enforced; audit→wire, replay→wire, transact inputs are not. The minimum R1 bar ("any external caller") is met; the aspirational bar is not. Phase 2 / Round 3 should tighten. | 7 |
| R3 F9 | `pyproject.toml` missing `hypothesis` / `pytest-asyncio` | **FIXED** | `pyproject.toml:21-25` — `dev = ["pytest>=7.0", "pytest-asyncio>=0.23", "hypothesis>=6.100"]`. Clean `pip install -e .[dev]` runs the full suite (463/463 green). | 10 |
| R3 F10 | `_tx_counter` module-level global | **FIXED (with TOCTOU caveat — see §2 F1)** | `src/persistence/fact/db.py:30` — comment "Transaction ids are allocated by the Store (see Store.next_tx), not by a module global". No `_tx_counter`, no `itertools.count(1)`. `src/persistence/fact/store.py:67` (protocol), `:120` (InMemoryStore: `max(d.tx for d in self._log)+1` under `self._lock`), `:199` (SQLiteStore: `SELECT COALESCE(MAX(tx),0)+1 FROM datom_log` under `self._lock`). `tests/fact/test_tx_allocation.py` has 8 tests including `test_two_sqlite_stores_on_same_file_do_not_collide`, `test_sqlite_round_trip_resumes_counter_correctly`, `test_db_module_has_no_module_level_tx_counter`. The shape fix is correct and the restore-from-disk case is handled. Concurrency TOCTOU documented separately in §2 as new finding R3-N1. | 8 |

**Tally:** 10 findings. 7 FIXED, 2 PARTIAL, 1 DEFERRED, 0 REGRESSED. Mean remediation grade
across 10 rows = **82.0 / 100** (= 8.2 / 10 per-finding), i.e. the class of fixes lives
at "pass the gate, one below excellence". No regressions from R1.

## 2. New composability findings (R3 round-2, scoped R3-N1..N4)

### R3-N1 — `Store.next_tx()` + `DB.transact` TOCTOU under concurrency [MAJOR]

**Trace:** `src/persistence/fact/db.py:129` allocates `tx = self.store.next_tx()`; line 211
calls `self.store.append(new_datoms)`. These are two separate `_lock` acquisitions. Between
them, another thread calling `transact` on the same `DB` (or on a sibling `DB` wrapping the
same `Store`) will see the same `max(tx) + 1` and allocate the same id.

**Reproduction (InMemoryStore):**
```
import threading
from persistence.fact.store import InMemoryStore
s = InMemoryStore()
allocated, barrier = [], threading.Barrier(5)
def w(): barrier.wait(); allocated.append(s.next_tx())
ts = [threading.Thread(target=w) for _ in range(5)]
for t in ts: t.start()
for t in ts: t.join()
# → allocated = [1, 1, 1, 1, 1]
```
Observed: 5 threads all allocate `tx=1`. The R1 F10 fix moved the hazard from
"shared module counter" to "two-step allocate-then-append on Store" — the failure mode is
different but the category (shared mutable state with non-atomic allocate) is the same.

**Why it matters for Phase 2:** `persistence.txn` (STM over refs) is expected to batch
across concurrent transactors. If two STM txns call `DB.transact` in parallel, both get
`tx=N`, and the `mark_invalidated(prior.tx, tx)` calls (db.py:213) both target the same
`tx` value — which means the predicate `WHERE tx = ?` matches either or neither row,
depending on append order. This quietly corrupts the bitemporal log.

**Fix proposal:** Either (a) make `Store` expose `allocate_and_append(facts) -> int` as an
atomic operation (hold `_lock` across `SELECT MAX(tx)+1` + `INSERT`), or (b) switch
`SQLiteStore` to `tx INTEGER GENERATED ALWAYS AS IDENTITY` and have `next_tx` read
back `lastrowid` after insert. The `threading.Lock` in store.py is insufficient for
this contract; the lock must span the `next_tx` → `append` → `mark_invalidated` triple.

**Test required:** `tests/fact/test_tx_allocation.py::test_concurrent_transacts_allocate_unique_txs` —
use a `threading.Barrier` to force the race, assert every transacted assert has a distinct tx.
Currently MISSING.

### R3-N2 — `wire.datom_to_wire` non-idempotent on already-colon `:datom/a` and pre-keyworded provenance source [MINOR]

**Trace:** `src/persistence/fact/wire.py:100` — `":datom/a": datom.a if datom.a.startswith(":") else ":" + datom.a`.
Inverse in line 143: `if isinstance(a, str) and a.startswith(":"): a = a[1:]`. So round-trip
of a bare `datom.a = "project/wacc"` → `":project/wacc"` → `"project/wacc"` is identity, but
round-trip of `datom.a = ":project/wacc"` → `":project/wacc"` → `"project/wacc"` is NOT
identity on the Datom dataclass. Same pattern on `_provenance_to_wire` for `:source`: a
pre-keyworded `provenance["source"] = ":already-keyworded"` survives `to_wire` untouched,
but `from_wire` strips its leading colon. So `wire_to_datom(datom_to_wire(d))` is not
pointwise equal to `d` on `d.provenance["source"]` if that value was pre-keyworded.

**Why it matters:** The adapter pair advertises itself as a boundary converter; composability
requires that `from_wire ∘ to_wire = id` on the Datom side. Today there's an implicit
precondition "`datom.a` and `provenance['source']` must be in their bare form," not
documented in the adapter's docstring. A Phase 2 caller who reads a Datom out of the DB,
transforms it into wire, and then tries to restore it will silently lose the leading colon
on those two fields.

**Fix proposal:** Either (a) normalise `Datom.a` and `provenance["source"]` to their bare
form in `Datom.__post_init__` (enforce the precondition), or (b) make `to_wire` a pure
mirror: strip any leading colon first, then always prepend — so the round-trip is idempotent
from the colon-prefixed form too. The docstring at `wire.py:62-71` should explicitly
declare the invariant either way.

### R3-N3 — `audit_entry_to_datom` produces wire but does not self-conform [MINOR]

**Trace:** `src/persistence/effect/handlers/audit.py:270-340` returns a wire dict without
calling `_S.parse(":persistence.fact/datom", wire)` inside the function. The test at
`tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec` asserts the
function's output does conform, but that's a one-time assertion at a specific call site —
any production path that emits a datom through `audit_entry_to_datom` without immediately
running it through `fact.wire.wire_to_datom` will miss the spec check. This is exactly
the R3 F8 gap — "enforce conformance at the boundary" — narrowly underfulfilled.

**Why it matters:** The parse-don't-validate discipline R1 F8 recommended only buys you
composability if it's invoked at the **producer**, not the consumer. Today it's invoked
only by `fact/wire.py`, which is a consumer for the audit-emitted wire (and a producer
for the Datom-derived wire). Symmetric coverage would have `audit_entry_to_datom`
self-conform its output before return, same as `datom_to_wire` does.

**Fix proposal:** Add `_S.parse(":persistence.fact/datom", result)` as the last
line of `audit_entry_to_datom` (three imports: `from persistence import spec as _S`). One
line; zero ambiguity; makes this function a spec-enforced boundary. Matches `wire.datom_to_wire`.

### R3-N4 — `AuditEntry.op` has no format invariant [MINOR]

**Trace:** `src/persistence/effect/handlers/audit.py:162-163`:
```python
"op": op_name,   # op_name is whatever wraps passed in
```
`op_name` flows into the content hash. If wraps was `(":llm/call",)` the entry's `op` and
hash embed `":llm/call"`; if wraps was `("llm/call",)` the entry embeds `"llm/call"` and
the hash differs — but both are accepted by the AuditEntry dataclass. `audit_entry_to_datom`
line 309 does `op_bare = entry.op.lstrip(":")` so both produce the same wire-side
`:audit/llm.call`, but the content hash that IS the chain link is colon-sensitive.
`datom_to_audit_entry` always reconstructs `op` as `":llm/call"` (line 356 `":" + a[len(":audit/"):].replace(".", "/")`),
so an entry whose original `op` was `"llm/call"` (no colon) cannot be losslessly round-tripped
via wire. This matches the R3-N2 identity-loss pattern but in a different module.

**Why it matters:** The test `tests/effect/test_public_surface.py:42` literally constructs
an `AuditEntry(op="llm/call", ...)` (no colon), suggesting the convention is not pinned. A
real Runtime-driven call produces `op=":llm/call"`. The mix works because the wire-level
encoding uniforms both, but the hash chain does not. Two systems emitting the same logical
op with different op-format conventions will have non-matching chain links.

**Fix proposal:** Make `AuditEntry.op` validated in `__post_init__` — reject anything
that doesn't match `_KEYWORD_RE`. Or, symmetrically, normalise to `":"+op.lstrip(":")` at
construction. Pin the convention.

## 3. Adapter-pair algebra check

### `wire.py` (datom_to_wire / wire_to_datom)

| Direction | Identity holds? | Failure mode |
|---|---|---|
| `wire_to_datom ∘ datom_to_wire` on a **bare-form** Datom (`a="foo/bar"`, provenance source bare) | **YES** | Verified by `tests/fact/test_wire.py::TestWireToDatom::test_roundtrip_preserves_fields` (line 84) — fields match exactly. |
| `wire_to_datom ∘ datom_to_wire` on a **colon-form** Datom (`a=":foo/bar"` or provenance source keyworded) | **NO** | The from-wire path strips the leading `:` unconditionally; the to-wire path preserves pre-keyworded values. See R3-N2 for the specific trace. |
| `datom_to_wire ∘ wire_to_datom` on a conformant wire dict with UUID `e` and int `tx` | **YES** | wire_to_datom coerces to Datom dataclass shape; datom_to_wire re-emits colon keys and op; both spec.parse checks pass. |
| `datom_to_wire ∘ wire_to_datom` on a content-hash-`tx` wire dict | **REJECTED** | `wire_to_datom` raises `TypeError` (wire.py:119) — by design, content-hash-tx wire is audit-side, not Fact-side; refusing silent coercion is correct. |

**Conclusion:** Adapter pair is identity on the canonical bare-form domain (which is the
domain Datom's `__post_init__` implicitly enforces, though nowhere documented). Partial
identity loss on the pre-keyworded subdomain. **Acceptable** for Round 2; document the
invariant and harden in Round 3.

### `verdicts.py` (as_edn / as_python)

| Direction | Identity holds? | Evidence |
|---|---|---|
| `as_python ∘ as_edn` on `PYTHON_VERDICTS` | **YES** | `tests/effect/test_verdicts.py::test_as_python_of_as_edn_is_identity` (parametrised over 6 vocabs, line 34). |
| `as_edn ∘ as_python` on `EDN_VERDICTS` | **YES** | Parametrised over 6 vocabs (line 51). |
| Idempotence: `as_edn(as_edn(v)) == as_edn(v)` on `EDN_VERDICTS` | **YES** | Parametrised (line 64-68). |
| Unknown verdict | **RAISES** | `ValueError` in both directions — by design. |

**Conclusion:** verdicts.py is a clean bijection on the union of the two vocabularies, with
loud failure on unknowns. **This is the cleanest adapter in the fix pass.**

## 4. Straggler audit — non-colon op names in live code

Search space: `src/persistence/**/*.py` and `tests/**/*.py`.

| Pattern | Live source | Tests | Docstrings / comments |
|---|---|---|---|
| `perform("llm/call"` (no colon) | 0 | 0 | — |
| `wraps={"llm/call"` or `wraps=("llm/call"` | 0 | 0 | 0 |
| Bare `"net/fetch"` / `"tool/call"` / `"clock/now"` / `"db/read"` string literal | 0 | 0 | 2 (both in **comments** describing the old R1 bug: `src/persistence/replay/effect_handler.py:35`, `src/persistence/effect/handlers/audit.py:304`) |
| `AuditEntry(op="llm/call", ...)` (no colon) | 0 | 1 (`tests/effect/test_public_surface.py:42`) | — |

**Conclusion:** Effectively zero stragglers in executable code. The two comment references are
intentional history markers explaining the R1 defect — they are documentation, not live
strings. The one test literal at `test_public_surface.py:42` is synthetic (constructs
an AuditEntry directly for an isinstance test) and is decoupled from dispatch; it's still a
convention violation (see R3-N4) and should be tightened to `":llm/call"`, but it does not
affect behaviour.

Catalog (`src/persistence/effect/catalog.py`): all 15 keys leading-colon
(`":llm/call"`, `":tool/call"`, `":mem/read"`, `":mem/write"`, `":decide"`, `":ask-user"`,
`":emit-artifact"`, `":sleep"`, `":random"`, `":env/read"`, `":net/fetch"`, `":secret/use"`,
`":cost/charge"`, `":clock/now"`, `":audit/emit"`).

## 5. Overall composability grade: **8.6 / 10**

- **+** Spec is now the single source of truth for the wire form; the fact↔wire boundary
  enforces it on every call (R3 F1, F3, F7, F8, F9, F10 — all the hard shape fixes).
- **+** Replay ↔ effect plugs together under a real Runtime; the load-bearing e2e test is
  the right invariant (record→hash, replay→hash, assert equal) and it's green without mocks.
- **+** verdicts.py is a clean algebra; `effect.__all__` is exhaustive; Mem0Interceptor
  matches real mem0ai 2.x.
- **+** 463 tests, +107 net over baseline, all green in 1.99s.
- **−** R3 F4 deferred; the migration SQL header still claims Postgres portability it does
  not deliver. Honesty issue, not just portability.
- **−** R3 F6 partial: the reconciler exists but only fires at the wire boundary; runtime
  producers still hand-write verdict literals.
- **−** R3 F8 partial: only fact↔wire conforms at the boundary; audit→wire, replay→wire,
  DB.transact inputs do not self-conform.
- **−** New defects R3-N1 (tx TOCTOU), R3-N2 (adapter identity loss on pre-keyworded input),
  R3-N3 (audit→wire not self-conforming), R3-N4 (AuditEntry.op unpinned). N1 is the serious one.

**Ladder jump:** 4.5 → 8.6 = **+4.1 points**, largest of any reviewer if round 2 holds. Target
≥ 8.5 met.

## 6. Go / no-go for Round 3

**GO**, with four concrete carries into Round 3:

1. **R3-N1 (tx TOCTOU)** — fix before declaring concurrency-safety anywhere in the paper.
   This is a real bug masked by the GIL under light load.
2. **R3 F4 / migration SQL** — either fix the AUTOINCREMENT (portable `GENERATED AS IDENTITY`),
   or strike the false "Portable between Postgres 14+ and SQLite 3.37+" claim from the
   SQL file header comment.
3. **R3 F8 tightening** — add `_S.parse(":persistence.fact/datom", result)` as the last
   line of `audit_entry_to_datom`; add `_S.parse(":persistence.replay/trajectory", edn)`
   as the last line of `Trajectory.to_edn`. One line each, closes the self-conform gap.
4. **R3-N2 / R3-N4 (adapter identity + AuditEntry.op convention)** — pin the `:datom/a`
   and `AuditEntry.op` format invariants in their respective `__post_init__` hooks.
   Small surface, removes silent-divergence risk for Phase 2.

None of these block Round 3; all are in the "8.6 → 9.3" territory per the ARIS ladder
(4 → 7 → 7.8 → 9.0 from MEMORY.md). The fix pass has delivered the biggest reviewer
jump by design. Proceed.
