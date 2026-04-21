# CHANGELOG — `persistence.spec`

Module-local changelog. Follows conductor methodology: every ship writes an
entry here; top-level `CHANGELOG.md` (if one is ever added) aggregates.

## 0.1.0 — 2026-04-20 — Module 6 initial ship

Phase 1 Workstream C of conductor track `persistence-os-foundation_20260420`.

Ship: the boundary-contract layer. Specs are predicate-generator pairs. Conform
returns a discriminated union (`Conformed | ConformError`) so downstream code
cannot accidentally consume raw, unvalidated input. Parse-don't-validate.

### Core surface

- `Spec` base class with `conform(value) -> Conformed | ConformError`,
  `generate()`, `explain(value)`, `to_edn()`.
- `Conformed` — frozen typed container (`.value`, `.spec_key`, `.is_ok == True`,
  `.unwrap()`).
- `ConformError` — structured failure with `reason`, `path`, `hint`, and
  recursive `sub_errors`; prepends path crumbs via `with_path()`.
- Exceptions inside predicates are caught and converted to a `ConformError`
  — callers never see a raw traceback from `conform()`.

### Primitives

`int_`, `float_`, `bool_`, `str_`, `bytes_`, `uuid_`, `inst`.

- No silent coercion anywhere. `int_` rejects `bool` (Python subclass trap),
  `float_` rejects `int`, `str_` rejects `bytes`.
- `uuid_` and `inst` *are* allowed to refine: they accept their canonical string
  forms and return the typed object (UUID / tz-aware datetime). Naive
  datetimes are rejected loudly.

### Combinators

`and_`, `or_`, `not_`, `maybe`, `keys`, `map_of`, `seq_of`, `tuple_of`, `enum`,
`regex`, `ref`.

- All combinators are frozen dataclasses — two structurally equal specs compare
  `==`, are hashable, go into sets. This is the "spec is a value" property
  (paper §4.6) cashed out in Python.
- `keys` implements open shape: extra keys pass through (Datomic/EDN
  convention). Nested errors carry path breadcrumbs.
- `seq_of` rejects `str`/`bytes` even though they're iterable — a common
  footgun.
- `ref(key)` resolves lazily against the registry, so specs may reference each
  other in either module-load order.

### Registry

- Process-wide spec registry keyed by namespaced keyword (e.g.
  `:persistence.fact/datom`).
- `register(key, spec)` swaps atomically; callers holding a ref to the old
  spec still work because specs are immutable values.
- `parse(key, value)` raises `SpecError` on mismatch; `conform(key, value)`
  returns the structured result.
- `explain_for_llm(key, value)` renders the LLM-ready self-healing hint.
- `quickcheck(key, prop, n)` runs property-based tests against the spec's
  own generator.

### Canonical specs (registered at import)

| key                                           | source                                 |
| --------------------------------------------- | -------------------------------------- |
| `:persistence.fact/datom`                     | agent1-fact-spec §1                    |
| `:persistence.effect/op`                      | agent3-effect-spec §1 (15-op catalog)  |
| `:persistence.effect/audit-entry`             | agent3-effect-spec §6                  |
| `:persistence.plan/node`                      | paper §4.3                             |
| `:persistence.plan/skill`                     | paper §4.3 (4-gate promotion)          |
| `:persistence.replay/trajectory`              | agent4-replay-spec §1                  |
| `:persistence.replay/fact`                    | agent4-replay-spec §1                  |
| `:persistence.replay/intervention`            | agent4-replay-spec §1                  |
| `:persistence.domain/decision`                | task brief                             |
| `:persistence.domain/wacc-assumption`         | task brief                             |

Every canonical spec has a working generator; `generate_example(key)` round-trips
through `conform` as a property-based test.

### LLM-friendly errors (self-healing hints)

`explain_for_llm` produces messages of the form:

```
Value failed :persistence.domain/decision — 2 key(s) failed.
- :persistence.spec/unit-float at :confidence: out of range [0,1]: 1.5. Fix: provide a float between 0.0 and 1.0 inclusive.
- :persistence.spec/non-empty-str at :rationale: string is empty. Fix: provide a non-empty string explaining the intent.
Fix: fix each sub-error listed above.
```

Every leaf error carries a `Fix:` clause. This is the format the Effect module's
`retry` handler will surface to the LLM for self-healing.

### Demo

`python -m persistence.spec.demo` prints 10 valid examples (generated) and 10
invalid examples (hand-crafted) with full LLM-ready explanations.

### Tests

152 tests, all green, across:

- `tests/spec/test_primitives.py` — 33 (scalars, coercion-safety, generate round-trip)
- `tests/spec/test_combinators.py` — 46 (each combinator + generator)
- `tests/spec/test_registry.py` — 15 (register/get/parse/quickcheck/version-swap/composition)
- `tests/spec/test_canonical.py` — 38 (per-spec positive/negative + parametrized round-trip)
- `tests/spec/test_llm_errors.py` — 7 (explanation format contract)
- `tests/spec/test_generative.py` — 13 (hypothesis-driven & spec equality)

### Dependencies

- `hypothesis` (stdlib-only otherwise). Used for the generative test harness.
- No Pydantic, no marshmallow, no typing hacks. Zero runtime deps outside stdlib
  + hypothesis for tests.

### Deviations from brief

None material. Minor scoping notes:

- `_AnyValueSpec` was added (not in the brief list) because `:datom/v`, plan
  node args, trajectory state, and audit entry args/results all need an
  "any EDN value" spec. Kept internal (not in `__all__`) and restricted to
  EDN-compatible types (str/int/float/bool/None/list/tuple/dict/UUID/datetime),
  so it does not become a typing escape hatch — unlike `Any`.
- `regex(pattern)` uses a small pure-stdlib generator that handles the idioms
  this module actually uses. The canonical specs that need richer regex
  generation (`:sha256:HEX`, `v\d+`, keyword pattern) use dedicated custom
  specs (`_Sha256Spec`, `_VersionSpec`, `_KeywordSpec`) that carry their own
  generators. This avoids an external `rstr` dependency without sacrificing
  round-trip fidelity for canonical specs.

### Next

Sibling workstreams (Fact, Effect, Replay) can now import
`persistence.spec` and depend on the canonical specs at their boundaries.
The Plan module will register its `:persistence.plan/node` recursive spec
more strictly once it lands (current shape uses `_any_value` in the children
slot to avoid circular dependency at import time).
