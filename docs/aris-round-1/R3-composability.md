# ARIS Round 1 — Reviewer R3 — Composability & Phase-2 Integration

**Commit:** `2c96fb7` on `main` · **Repo:** `/Users/nawfalsaadi/Projects/persistence-os/`
**Reviewer lens:** cross-module contracts, hidden coupling, Phase-2 integration readiness.

## Summary grade: 4.5 / 10

The four modules are individually clean and their test suites pass in isolation, but
the *interfaces between them are fiction*. Every cross-module schema is encoded in a
different dialect: Fact's `Datom` uses snake\_case Python attributes with `tx:int` and
tz-aware `datetime`; Effect's `audit_entry_to_datom` emits a dict with slash-prefixed
string keys (`"datom/e"`), string `tx` (a sha256), and float `tx_time` (epoch seconds);
Spec's `:persistence.fact/datom` requires EDN keywords (`":datom/e"`, with leading colon),
UUID for `e`, `int` for `tx`, and a tz-aware `datetime` for `tx-time`. Three schemas,
zero adapters. The "audit → fact" round-trip test the README claims (see
`tests/effect/test_audit.py:112-122`) is a round-trip **within Effect's own dict shape**;
it never calls `fact.DB.transact()` and never calls `spec.conform(":persistence.fact/datom", ...)`.
If Phase 2 wires them together naively, the first dispatch will `KeyError` on `e` in
`DB.transact` and every spec-conform call will fail because every required EDN key is
missing from the Python dicts both modules produce. Combined with the bogus `AUTOINCREMENT`
in the shared migration SQL (SQLite-only, Postgres rejects it), the broken `mem0` kwarg
shape in `Mem0Interceptor`, the empty `__all__` in `persistence.effect`, and `hypothesis`
missing from `pyproject.toml` altogether, Phase 1 is a set of four good prototypes that
have not been tested as a stack. Target gate of ≥7.0 is not met.

## Cross-module contract audit

| Contract | Status | Risk |
|---|---|---|
| `effect.audit` → `fact.Datom` shape | **FAIL** | `audit_entry_to_datom` emits string `tx` (sha256), float `tx_time` (epoch seconds), and keys like `"datom/e"`. `fact.DB.transact` wants `{"e","a","v","valid_from",…}` with `int tx` + tz-aware `datetime`. Direct wiring raises `KeyError`. |
| `replay.EffectHandler` ↔ `effect.Runtime` | **FAIL** | Different op namespace (`:net/fetch` vs `net/fetch`), different signature shape (`call(op, args, fn)` thunk vs Handler `(args, k, ctx)` continuation), different context model (ContextVar Runtime vs dataclass state). Not drop-in. |
| `spec` canonical coverage vs what modules actually emit | **FAIL** | Spec requires EDN-keyword keys (`":datom/e"`, `":trajectory/id"`). Fact and Replay emit snake\_case Python attributes. `conform()` against any canonical spec will fail on every live value the other modules produce. |
| `policy` verdict ↔ `spec.ConformResult` | **GAP** | Parallel type systems. `policy_eval.evaluate()` returns `{"verdict":str,"reasons":list}`; spec returns `Conformed | ConformError`. No adapter exists. Verdict vocabularies also disagree (see F6). |
| `fact.transact()` multi-datom | **PASS (barely)** | Signature accepts `list[dict]`, so `txn` (Phase 2) batching works. But the dict keys aren't the ones `effect` emits — so the STM → Fact → Effect audit loop is still broken end to end. |
| `__all__` / module surface | **FAIL** | `persistence.effect.__init__.py` is `__all__ = []` — zero public names. Callers must import submodules. Phase 2's `plan` cannot do `from persistence.effect import perform, Runtime, make_audit_handler`. |

## Findings

### F1 — Three incompatible datom schemas [CRITICAL] [contract: Fact/Effect/Spec]

**Trace:** Phase-2 wiring intent — `effect.audit` handler fires on `llm/call`; the
collected `AuditEntry` must flow into `fact.DB.transact(...)` as a datom and also be
validatable via `spec.conform(":persistence.fact/datom", d)`. That is the only reason
both schemas exist.

**Break point:**
1. `src/persistence/effect/handlers/audit.py:198-222` — `audit_entry_to_datom` returns
   a dict with keys `"datom/e","datom/a","datom/v","datom/tx","datom/tx-time",
   "datom/valid-from","datom/valid-to","datom/op","datom/provenance","datom/invalidated-by"`.
   `datom/tx = entry.id` (a sha256 string), `datom/tx-time = entry.recorded_at` (a
   float from `clock/now`), `datom/op = "assert"` (no leading colon).
2. `src/persistence/fact/db.py:67-88` — `DB.transact(facts)` iterates `fact["e"]`,
   `fact["a"]`, `fact["v"]`, `fact.get("valid_from")`, `fact.get("op","assert")`.
   **None of the keys match.** Even if you prefix-strip `datom/`, `tx-time` vs
   `valid_from` still disagrees, and `Datom.__post_init__` (see `datom.py:55-66`)
   raises `ValueError` the moment the float `recorded_at` is fed into `tx_time`
   (expects tz-aware `datetime`), and again when the string sha256 is fed into `tx`
   (expects `int`).
3. `src/persistence/spec/_canonical.py:197-214` — `:persistence.fact/datom` spec
   requires keys `":datom/e"`, `":datom/a"`, etc. (with leading colon). Effect's
   emitted dict uses `"datom/e"` (no colon) — every required field is missing under
   the spec's naming convention, so `spec.conform(":persistence.fact/datom", effect_datom)`
   reports all 9 keys absent.

**Why:** The three modules were built against three different readings of the same
doc. `agent1-fact-spec.md §1` describes the 8-tuple in EDN (`:datom/e`); the fact
module translated EDN keywords to Python identifiers (`e`); the effect module
preserved the keyword-minus-colon form (`datom/e`); the spec module preserved the
full EDN keyword (`":datom/e"`). No adapter module was defined.

**Fix proposal:**
1. Add `src/persistence/fact/datom.py::Datom.from_edn(d: dict) -> Datom` and
   `Datom.to_edn() -> dict` that convert between the Python and `":datom/..."`
   shapes, deferring to `spec.parse(":persistence.fact/datom", d)`.
2. Rewrite `audit_entry_to_datom` to emit the EDN-keyword shape (leading colons),
   convert `recorded_at` to tz-aware `datetime`, and populate `tx` as the integer
   sequence from a `tx/allocator` effect (not the content hash — that belongs in
   `:datom/provenance[:signature]`).
3. Add one integration test in `tests/integration/test_audit_to_fact.py`:
   ```python
   rt = Runtime([...])
   with with_runtime(rt): perform("llm/call", ...)
   datom_edn = audit_entry_to_datom(entries[0])
   spec.parse(":persistence.fact/datom", datom_edn)  # must not raise
   db = DB().transact([Datom.from_edn(datom_edn)])   # must not raise
   ```
   That one test would have caught this before merge.

### F2 — `replay.EffectHandler` cannot swap under `effect.Runtime` [CRITICAL] [contract]

**Trace:** Phase-2 intent (per `docs/agent4-replay-spec.md §6`) — install
`replay.EffectHandler` as a layer under the production handler stack so the engine
can drive `replay()` with the *same* agent body. Today the agent body is expected
to call `effect.perform("llm/call", **args)`; in replay mode that call needs to be
redirected to the recorded cache.

**Break point:**
1. `src/persistence/replay/effect_handler.py:18-27` — `NON_REPLAYABLE_OPS =
   frozenset({":net/fetch", ":tool/call"})`, `PROMPT_HASH_OPS = frozenset({":llm/call"})`.
   **All have leading colons.**
2. `src/persistence/effect/catalog.py:38-106` — catalog keys are `"llm/call"`,
   `"net/fetch"`, `"tool/call"`. **No leading colons.**
3. `src/persistence/effect/runtime.py:144` — `perform("llm/call", **args)` is the
   production call site; if that op reaches a replay layer the `op in
   NON_REPLAYABLE_OPS` check compares `"llm/call" ∈ {":llm/call", ":net/fetch"…}`
   and fails — the guard silently does not trigger, and a `:net/fetch` with no
   cache entry would be allowed to re-execute (violating §8 honest-constraint).
4. Shapes also disagree: `EffectHandler.call(op, args, fn)` takes a zero-arg thunk
   `fn`, but `effect.Handler` clauses have signature `clause(args, k, ctx)` where
   `k` is a continuation that itself dispatches down the stack. There's no adapter.

**Why:** Replay was built against the spec doc EDN conventions; Effect was built
against Python-identifier conventions. No synchronization step happened.

**Fix proposal:**
1. Canonicalize op names in ONE place. My recommendation: effect keeps `"llm/call"`
   (no colon) as the internal representation; replay and spec code compare by
   stripping a leading `:`. Add `effect.catalog.normalize_op(op: str) -> str`.
2. Write an adapter `replay.install_as_effect_handler(eh: EffectHandler) ->
   effect.Handler` that wraps each NON\_REPLAYABLE\_OP catalog key in an effect
   clause whose body invokes `eh.call` with the continuation `k` as `fn`.
3. Add an integration test: same agent body producing the same action trace under
   `Runtime([raw, clock, audit])` (record) and `Runtime([replay_intercept(eh),
   clock_replay, audit])` (replay). The NO-OP-intervention trajectory hash must
   match between the two Runtimes, not just within the replay engine's own loop.

### F3 — Trajectory cannot conform to its own spec [CRITICAL] [contract: Replay/Spec]

**Trace:** Phase-2 plan-level reasoning ("Is this recorded trajectory well-formed
before we DPO over it?") requires `spec.conform(":persistence.replay/trajectory",
traj.to_dict())`.

**Break point:**
- `src/persistence/replay/trajectory.py:72-87` — `Trajectory.to_dict()` emits keys
  `id`, `parent_id`, `branch_point`, `agent`, `goal`, `seeds`, `started_at`, …
- `src/persistence/spec/_canonical.py:431-451` — `:persistence.replay/trajectory`
  requires `":trajectory/id"`, `":trajectory/parent-id"`, `":trajectory/branch-point"`,
  `":trajectory/agent"`, `":trajectory/goal"`, `":trajectory/seeds"`,
  `":trajectory/started-at"`, …

Every required key is missing. Further: spec requires `:trajectory/started-at:
inst()` (tz-aware datetime); replay's `started_at` is `Optional[str]` defaulting to
`None`. Spec requires `:trajectory/hash: str_()`; replay's `hash` starts `None` and
only gets a value if `trajectory_hash()` is called explicitly. Spec requires
`:trajectory/seeds: {":llm", ":tool", ":env"}`; replay's seeds use `"llm"`, `"tool"`,
`"env"` (no colons).

**Why:** Same doc-drift root cause as F1.

**Fix proposal:** Add `Trajectory.to_edn() / from_edn()` that do the key-name
translation and drop-fill defaults. Gate every `record()` completion through
`spec.parse(":persistence.replay/trajectory", traj.to_edn())`. If it raises, the
record is malformed — loud failure beats silent-bad-data every time.

### F4 — SQLite-only migration claims Postgres portability [MAJOR] [module: Fact]

**Trace:** Operator follows `docs/memory-palace-integration.md §2` and runs
`psql "$DATABASE_URL" -f migrations/0001_datom_log.sql`.

**Break point:** `src/persistence/fact/migrations/0001_datom_log.sql:20` —
```sql
seq INTEGER PRIMARY KEY AUTOINCREMENT,
```
`AUTOINCREMENT` is a SQLite keyword. Postgres 14+ rejects it at parse time with
a syntax error. Postgres wants `GENERATED ALWAYS AS IDENTITY` or `BIGSERIAL`.
The SQL file comment literally claims "Portable between Postgres 14+ and SQLite
3.37+" (line 2) — that is false.

**Why:** The file was likely tested only against `sqlite3 file.db <
0001_datom_log.sql`. No CI job runs it against a Postgres container.

**Fix proposal:** Either split into two files (`0001_datom_log.sqlite.sql` and
`0001_datom_log.postgres.sql`) or use portable DDL that both accept:
```sql
CREATE TABLE IF NOT EXISTS datom_log (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ...
);
```
(Postgres 10+ and SQLite 3.37+ both accept `GENERATED … AS IDENTITY`; SQLite
treats it as a sequence.) Add `tests/fact/test_postgres_migration.py` that boots
a Postgres via testcontainers and applies the SQL — without that test, the
claim should not be in the doc.

### F5 — `Mem0Interceptor.add` will TypeError on real `mem0.Memory` [MAJOR] [module: Fact]

**Trace:** User follows `docs/memory-palace-integration.md §1` literally on the VPS.

**Break point:** `src/persistence/fact/interceptors/mem0_adapter.py:72-74`:
```python
return self.mem0.add(e=e, a=a, v=v, valid_from=vf, **legacy_kwargs)
```
Upstream `mem0.Memory.add` signature is `add(messages, *, user_id=None, agent_id=None,
run_id=None, metadata=None, memory_type=None, prompt=None, infer=True)`. It has
no `e`, no `a`, no `v`, no `valid_from`. The real client raises `TypeError:
add() got an unexpected keyword argument 'e'` on the first call.

The test suite never catches this because every test uses a fake mem0 that
accepts `**kwargs` unconditionally — see the duck-typed fixtures.

**Why:** No upstream API check was done during build. The memory-palace-integration
doc's example code hints at the problem: the example forwards `messages=`,
`user_id=`, `metadata=` through `**legacy_kwargs` — but the interceptor also
forwards `e=, a=, v=, valid_from=` explicitly, which mem0 will reject.

**Fix proposal:** Rewrite `Mem0Interceptor.add/update` to NOT forward `e/a/v/valid_from`
to the mem0 client. Keep them for the datom emission only. The interceptor's
kwargs should be `add(*, e, a, v, valid_from=None, provenance=None, **mem0_kwargs)`
and the call to mem0 should be `self.mem0.add(**mem0_kwargs)`. Then add a
contract test against a MINIMAL fake that mirrors the *real* `mem0.Memory.add`
signature (messages+user\_id+metadata only).

### F6 — Three verdict vocabularies, no reconciler [MAJOR] [contract: Effect/Spec]

**Trace:** Phase-2 policy pipeline: policy handler fires → verdict lands in audit
entry → audit entry conforms to `:persistence.effect/audit-entry` spec.

**Break point:**
- `src/persistence/effect/policy_eval.py:157` — `verdict` values: `"allow" | "deny" |
  "deny-silently" | "require-approval"` (no colons).
- `src/persistence/effect/handlers/audit.py:43` — `AuditEntry.verdict` strings:
  `"ok" | "error" | "deny" | "deny-silently" | "require-approval"` (no colons;
  adds `"ok"` and `"error"`, doesn't include `"allow"`).
- `src/persistence/spec/_canonical.py:247` — spec enum: `":allow" | ":deny" |
  ":deny-silently" | ":require-approval" | ":ok" | ":error"` (WITH colons).

If a policy-denied call is audited, the audit entry's verdict is `"deny"` (from
policy) or `"error"` (from the audit handler's exception path, `audit.py:116`) —
spec requires `":deny"` / `":error"`. Conform fails even if we fix F1.

**Fix proposal:** Define `persistence.spec.verdicts` with a single source-of-truth
enum and `as_python()` / `as_edn()` helpers. Have `policy_eval.evaluate()` and
`AuditEntry` both produce the same type. Spec's enum should match whichever form
is chosen.

### F7 — `persistence.effect` has empty `__all__` [MAJOR] [module surface]

**Trace:** Phase-2 `plan` module imports `from persistence.effect import perform,
Runtime, with_runtime, Handler, make_audit_handler, ...`.

**Break point:** `src/persistence/effect/__init__.py:10` — `__all__ = []`. The
import works (Python doesn't enforce `__all__`), but:
1. IDE auto-import never surfaces `effect.perform`.
2. `from persistence.effect import *` imports nothing.
3. Consumers must reach into private-ish submodules
   (`persistence.effect.runtime`, `persistence.effect.handlers.audit`), which
   means every rename inside effect silently breaks downstream.

By contrast, `persistence.fact.__init__.py`, `persistence.spec.__init__.py`, and
`persistence.replay.__init__.py` all re-export their public surface. Effect is
the outlier.

**Fix proposal:** Populate `persistence.effect.__init__.py` with the stable public
surface:
```python
from persistence.effect.runtime import (
    Effect, Handler, Runtime, Unhandled, perform, named, mask, with_runtime
)
from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.effect.catalog import CATALOG, OP_NAMES, validate_args
from persistence.effect.handlers.audit import (
    AuditEntry, make_audit_handler, verify_chain,
    audit_entry_to_datom, datom_to_audit_entry,
)
# …and the other handler factories
__all__ = [...]
```

### F8 — Spec is a disconnected island [MAJOR] [cross-module]

**Trace:** Search for callers of `spec.conform()` / `spec.parse()` across the
four modules.

**Break point:** `grep conform(` returns **zero hits outside `src/persistence/spec/`**.
The parse-don't-validate discipline the spec module documents (see
`src/persistence/spec/CHANGELOG-spec.md:40` and `project_overview` memory) is
declared but not practiced. No boundary between Fact/Effect/Replay currently
conforms its input or output.

**Why:** Each module was built in isolation, couldn't import spec (would have
been cross-workstream coupling per the brief), and the integration pass that
was supposed to follow hasn't happened.

**Fix proposal:** This is not a Phase-1 defect to fix *today*, but Phase 2 must
make conformance the price of admission at every cross-module boundary:
- `fact.DB.transact` conforms each fact dict through `:persistence.fact/datom`.
- `effect.audit.audit_entry_to_datom` conforms its emission before returning.
- `replay.record()` conforms the final `Trajectory` before returning.
- `plan.eval` conforms its AST input at parse time.
Absent that, the spec module is dead weight — 152 tests that prove only that
specs conform to themselves.

### F9 — `pyproject.toml` missing runtime deps [MAJOR] [packaging]

**Trace:** New contributor runs `pip install -e .[dev]` and then `pytest`.

**Break point:** `pyproject.toml:21-23` — `dev = ["pytest>=7.0"]`. But:
- `tests/spec/test_generative.py` uses `hypothesis` (13 tests).
- `docs/plans/...` referenced `pytest-asyncio` in some specs; effect/runtime
  tests may need it.
- `persistence-fact-module-shipped-20260420` memory says "65 tests green on
  both backends"; Postgres backend needs `psycopg[binary]` — correctly in
  `[postgres]` extra, but the test matrix isn't wired to pull it.

Install-from-clean fails before the spec tests can run. Then `pytest` fails
cryptically with `ModuleNotFoundError: No module named 'hypothesis'`, and the
operator wonders whether the repo is broken or their env is.

**Fix proposal:**
```toml
[project.optional-dependencies]
postgres = ["psycopg[binary]>=3.1"]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.23",
    "hypothesis>=6.100",
]
```
Add a `tests/test_install.py` CI canary that imports every module and runs one
test from each submodule to catch regressions.

### F10 — `_tx_counter` is a module-level global [MAJOR] [Phase-2 txn contract]

**Trace:** Phase-2 `txn` module (STM over refs) batches writes through
`fact.DB.transact`. Two concurrent txns allocate `tx` ids.

**Break point:** `src/persistence/fact/db.py:32` — `_tx_counter = itertools.count(1)`.
Module-level. No lock. Test conftest "resets it between tests so ids are
predictable" (comment on line 33-34) — which is exactly the **shared mutable
state** kind of coupling R3's brief flagged. In production:
1. Two threads transacting simultaneously on the same `DB` get sequential but
   interleaved `tx` ids, which is fine *until* the first threading bug tries to
   order them by `tx_time`.
2. Two processes (single-writer Postgres deployment is "the simplest thing that
   works" — see comment line 33) each have their own counter starting at 1 and
   will collide catastrophically.
3. A `DB` built from a restored SQLiteStore starts `_tx_counter = count(1)` even
   if the log's max `tx` is 1000 — the next transact writes `tx=1` again and
   breaks every uniqueness assumption the indexes rely on.

**Fix proposal:** Make `tx` allocation a `Store` responsibility. `Store.next_tx()
-> int` that for `InMemoryStore` is `max(self._log, key=lambda d: d.tx).tx + 1`
and for `SQLiteStore` reads `SELECT coalesce(max(tx), 0) + 1 FROM datom_log`
under the same lock as `append`. Remove the module-level counter. This also
decouples Phase 2 `txn` from the Phase 1 counter — STM batches become atomic
from the store's perspective.

### F11 — Spec registry is a shared global without isolation [MINOR] [concurrency]

`src/persistence/spec/_registry.py:15` — `_REGISTRY: dict[str, Spec] = {}`.
Test suites that reload `persistence.spec` (for hypothesis strategies, say) race
against the module-load-time `register(...)` calls in `_canonical.py`. Not a
Phase-1 blocker; flag for Phase 3 (`repl` needs to inspect live spec maps under
a running agent).

## Import graph audit

Search: `from persistence\.(fact|effect|spec|replay)` across all modules.

- `persistence.fact` imports: only from `persistence.fact.*` (self-contained).
- `persistence.effect` imports: only from `persistence.effect.*` (self-contained).
- `persistence.spec` imports: only from `persistence.spec.*` (self-contained).
- `persistence.replay` imports: only from `persistence.replay.*` (self-contained).

**No cross-module imports. Zero circular deps. Clean.** This is the one big win:
each worktree's isolation was respected cleanly. But see F8 — the price paid is
that no module currently USES any other, so every cross-module contract is
asserted in doc comments and hope.

## Missing-for-Phase-2 contracts

Ranked by blast radius (high → low).

1. **`Datom.from_edn` / `Datom.to_edn` adapter.** Without it, `plan` can't
   schedule a write, `effect.audit` can't emit a datom, `replay` can't embed
   facts in trajectories. Blast radius: all four Phase-2 modules.
2. **`effect.handlers.replay_intercept(eh: replay.EffectHandler) ->
   Handler`.** Without it, replay is a closed loop — you can never run a
   production agent under record+replay without rewriting it. Blast radius: all
   of `replay` and the post-trade cron.
3. **`effect` public surface.** `plan` at minimum needs `perform`, `Runtime`,
   `with_runtime`, `Handler`, `mask`, and the audit handler factory. Blast
   radius: `plan` and `txn`.
4. **`fact.DB.next_tx()` (or `Store.next_tx`) contract.** STM needs atomic tx
   allocation. Blast radius: `txn`, plus every concurrent caller.
5. **Verdict-type reconciler.** Policy → Audit → Spec all disagree. Blast
   radius: audit log conformance, regulator replay.
6. **"Currently executing agent" handle.** Phase-3 `repl` needs to inspect a
   running agent's handler stack, current mask frame, active policy. Today the
   ContextVar-scoped `_active: Runtime` is opaque — there's no introspection
   API. Need `persistence.effect.runtime.current() -> Runtime | None` that is
   safe to call from outside `perform`. Blast radius: `repl` entirely.
7. **Backfill / replay-of-legacy-mem0 adapter.** Memory-Palace doc says Phase 2
   "backfills historical memories"; no skeleton exists. Blast radius: the
   whole ai-box integration milestone.

## What composes cleanly

1. **Runtime → Handler dispatch model (`effect.runtime`) is genuinely clean.**
   The ContextVar-scoped `_active` (`runtime.py:195-197`) makes threading safe;
   the explicit `with_runtime(rt)` block is unambiguous; `mask()` with a set-of-
   sets frame stack (`runtime.py:216-228`) is the right primitive. `replay`'s
   handler can be wired here once F2 is fixed.
2. **`fact.DB` is a VALUE, not a mutable object** (`db.py:55-82`). Every op
   returns a new `DB`; branching clones into a fresh store. This is exactly
   what `txn` needs — STM refs can hold `DB` references and compare-and-swap
   them without deep copies. Phase 2 STM will integrate naturally.
3. **Spec combinators are well-factored** (`_combinators.py` keeps 11
   combinators orthogonal, `ref()` resolves lazily so specs can forward-
   reference each other). `plan` adding `:persistence.plan/…` specs is a
   three-line exercise IF the base keyword-prefix convention is respected.

## Residual risks

- **The 356-test green banner is misleading** for Phase 2. Tests prove modules
  work *within their own assumption space*. None of them test the assumption
  space *between* modules. The Phase-2 team will hit all of F1–F6 at once on
  the first integration attempt. **Recommendation: Round-2 exit gate requires
  `tests/integration/test_phase1_composition.py` covering audit→fact, record→
  replay→compare under the real `effect.Runtime`, and at least one
  `spec.parse` on every emitted cross-boundary value.**
- **The memory-palace-integration doc will fail on the VPS** when a user
  follows it literally (F4 + F5). Either fix the code or amend the doc with
  a shim layer example — but someone is going to run the VPS test plan in
  §4 as-written before you ship Phase 2, and they're going to hit both bugs
  inside the first 10 minutes.
- **No cross-module CI matrix.** pyproject has `[dev]` and `[postgres]` but
  nothing wires "`pip install -e .[dev,postgres] && pytest tests/`" into a
  test job that runs against Postgres + SQLite both. If Phase 2 adds more
  backends (Kuzu, DuckDB), the dialect-drift problem from F4 multiplies.
- **No contract-level fuzz test.** `hypothesis` is used only inside `spec`
  for self-conformance. The high-leverage use — "generate a random
  `Trajectory`, serialize, reparse, conform — they should all agree" — is
  not exercised. That's the test that would have caught F3 pre-merge.

Score: **4.5 / 10**. Fix F1, F2, F3, F4, F5, and F7 (all code-level, ≤ 300 LOC
total) and this becomes a 7.5/10. Without those fixes, Phase 2 will spend its
first week on adapter glue it shouldn't have to write.
