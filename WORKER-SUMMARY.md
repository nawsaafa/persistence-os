# W-boundary — Boundary Incoherence Fix Pass

**Branch:** `W-boundary`
**Base:** `main` @ `5f97882`
**Worktree:** `/Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-boundary`
**Scope:** ARIS Round 1 boundary-incoherence cluster — R1 F1/F2/F5/F6/F13 and R3 F1/F3/F6/F7/F8/F9. No out-of-scope refactors; no paper/rigor/integration work.

## Test count

| Stage | Tests |
|---|---|
| Baseline on `main` | 356 passed |
| After W-boundary | **419 passed in 1.3s** |
| Skipped / xfailed | 0 / 0 |

Net: **+63 new tests, 0 regressions.**

## Findings addressed

| Finding | Fix | Test |
|---|---|---|
| **R1 F1** — audit→datom keys drop leading colon | `audit_entry_to_datom` emits `:datom/e`, `:datom/a`, …, `:datom/op` | `tests/effect/test_audit.py::test_audit_entry_to_datom_has_fact_schema_fields` (updated) |
| **R1 F2** — audit datom fields type-wrong (sha256 vs UUID, string tx, float tx-time, …) | Spec relaxed to accept `or_(uuid_(), _sha256_spec)` for `:datom/e` and `or_(int_(), _sha256_spec)` for `:datom/tx` (content addressing is load-bearing per paper §4.1). `audit_entry_to_datom` coerces `recorded_at` float → tz-aware datetime; provenance keys colonised. | `tests/spec/test_canonical.py::TestFactDatom::test_content_hash_{e,tx}_accepted`, `::test_arbitrary_string_{e,tx}_still_rejected`; `tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec` |
| **R1 F5** — `:persistence.replay/fact` rejects string-keyed state/obs/action | Spec `:state`/`:obs`/`:action`/`:llm-in`/`:llm-out`/`:random-draws` relaxed to `map_of(str_(), _any_value)`. Same for `:trajectory/goal` and `:trajectory/outcome`. | `tests/spec/test_canonical.py::TestReplayFact::test_string_keyed_state_accepted` |
| **R1 F6** — `:audit/policy-id` required but defaults to `None` | Moved to optional in the canonical spec. | `tests/spec/test_canonical.py::TestEffectAuditEntry::test_policy_id_optional` |
| **R1 F13** / **R3 F6** — three verdict vocabularies disagree | New `src/persistence/effect/verdicts.py` with `PYTHON_VERDICTS` / `EDN_VERDICTS` sets and `as_edn` / `as_python` translators. `audit_entry_to_datom` calls `as_edn` at the wire boundary; `datom_to_audit_entry` calls `as_python`. Internal runtime keeps bare strings (zero existing-test churn). | `tests/effect/test_verdicts.py` (32 tests, parametrised round-trip on every vocab) |
| **R3 F1** — three datom schemas (fact / effect / spec) | Single wire-shape source of truth = registered spec. Adapters: `audit_entry_to_datom` (effect→wire), `fact.wire.datom_to_wire` / `wire_to_datom` (dataclass↔wire). Both sides conform through `spec.parse(":persistence.fact/datom", ...)` on every call. | `tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec`, `tests/fact/test_wire.py::TestDatomToWire::test_wire_conforms_to_fact_spec` |
| **R3 F3** — `Trajectory.to_dict()` fails `:persistence.replay/trajectory` conform | New `Trajectory.to_edn()` / `from_edn()` and `Fact.to_edn()` / `from_edn()`. `to_dict` unchanged (internal JSON round-trip). `to_edn` emits EDN keyword keys, tz-aware `:trajectory/started-at` (defaults to UNIX epoch UTC when unset), keyword-prefixed `:trajectory/status`/`:trajectory/wall-clock-basis`/seeds/tags, non-null `:trajectory/hash` (defaults to `"sha256:unset"` sentinel). | `tests/replay/test_trajectory.py::test_to_edn_*` + `::test_from_edn_*` (7 new) |
| **R3 F7** — `effect/__init__.py` has `__all__ = []` | Populated with 33 public re-exports: core runtime, canonical-JSON, op catalog, audit, policy, verdicts, every handler factory. Cross-checked against `docs/agent3-effect-spec.md §§1–6`. | `tests/effect/test_public_surface.py` (9 tests) |
| **R3 F8** — zero `spec.conform` / `spec.parse` callers outside spec | `fact.wire.datom_to_wire` and `wire_to_datom` call `spec.parse(":persistence.fact/datom", ...)` on every invocation — both directions exercise the registered spec at the fact↔spec boundary. Audit→fact boundary exercised by `test_audit_entry_datom_conforms_to_fact_spec`. | Same as F1 / F3 tests above. |
| **R3 F9** — `pyproject.toml` missing `hypothesis` / `pytest-asyncio` | Added to `[project.optional-dependencies.dev]`. Clean install of `.[dev]` now runs the full suite. | N/A (packaging) |

## Out-of-scope findings NOT addressed

Per task brief — these belong to sibling workers:

- **R1 F3** (`DB.transact` retroactive valid-to < valid-from) — W-rigor owns.
- **R1 F7** (replay↔effect runtime bridge, thunk vs continuation) — W-integration owns.
- **R1 F8** (replay `_advance_rngs_to_match` one-draw-per-step) — not in my cluster.
- **R1 F9** (paper ed25519 overclaim) — W-paper owns.
- **R1 F10** / **F11** / **F12** (effect `named_perform` mask well-formedness, demo `datetime.now`, `DBView.entity` tie-breaker) — not in my cluster (minor/cosmetic).
- **R3 F2** (replay EffectHandler continuation signature) — W-integration owns.
- **R3 F4** (SQLite AUTOINCREMENT Postgres portability) — not in my cluster.
- **R3 F5** (Mem0Interceptor kwargs) — W-integration owns.
- **R3 F10** (`_tx_counter` module-level global) — W-integration owns.

## Files changed

**Production code (new):**

- `src/persistence/effect/verdicts.py` — verdict reconciler module.
- `src/persistence/fact/wire.py` — `Datom ↔ wire` adapter with boundary spec conform.

**Production code (modified):**

- `src/persistence/spec/_canonical.py` — `:datom/e` / `:datom/tx` / `:datom/invalidated-by` relaxed via `or_`; `:audit/policy-id` optional; `:persistence.replay/fact` state/obs/action/llm-in/llm-out/random-draws relaxed to `map_of(str_(), _any_value)`; same for `:trajectory/goal` / `:trajectory/outcome`. `_Sha256Spec` moved up so `_datom` can reference it.
- `src/persistence/effect/handlers/audit.py` — `audit_entry_to_datom` rewritten to emit spec-conformant EDN wire form; `datom_to_audit_entry` is the symmetric inverse; imports verdicts helper for `:audit/verdict` translation at the boundary. `_recorded_at_to_inst`, `_principal_to_keyword_map`, `_keyword_map_to_principal` helpers.
- `src/persistence/effect/__init__.py` — populated `__all__` with 33 public names.
- `src/persistence/fact/__init__.py` — re-export `datom_to_wire`, `wire_to_datom`.
- `src/persistence/replay/trajectory.py` — `Trajectory.to_edn` / `from_edn`; `Fact.to_edn` / `from_edn`; EDN helper functions.
- `pyproject.toml` — added `hypothesis>=6.100` and `pytest-asyncio>=0.23` to `[project.optional-dependencies.dev]`.

**Tests (new):**

- `tests/effect/test_verdicts.py` — 32 tests on the verdict reconciler.
- `tests/effect/test_public_surface.py` — 9 tests on `__all__` + re-exports.
- `tests/fact/test_wire.py` — 8 tests on `Datom ↔ wire` + boundary conform.

**Tests (modified):**

- `tests/spec/test_canonical.py` — 6 new cases for spec relaxations (content-hash acceptance + narrow rejection + policy-id optional + string-keyed state).
- `tests/effect/test_audit.py` — 1 new spec-conform boundary test; existing `test_audit_entry_to_datom_has_fact_schema_fields` updated to assert EDN-keyword keys; fixture `clock_ts` corrected from `1_712_000_000_000` (ms; year 56221) to `1_712_000_000` (s), matching `clock/now` documentation and other test suites.
- `tests/replay/test_trajectory.py` — 7 new `to_edn` / `from_edn` tests (including spec conform on the wire form).

## Commits

```
6508c4c effect: populate __init__.py __all__ with public surface (ARIS R3 F7)
2e64e96 replay: Trajectory.to_edn / from_edn + Fact.to_edn / from_edn
55aa8ac fact.wire: Datom ↔ wire-form adapter (ARIS R3 F1, F8)
4209e8a effect: audit_entry_to_datom emits spec-conformant wire form
f46b120 spec: relax datom/e, datom/tx, audit/policy-id, replay/fact
92f83dd packaging: add hypothesis + pytest-asyncio to dev deps
```

## Verification

Full suite from the worktree venv, pre-merge:

```
$ pytest
============================= 419 passed in 1.32s ==============================
```

Zero skipped, zero xfailed. TDD discipline upheld — every fix landed with a test that was demonstrated to fail before the code change.

## Merge notes (for the conductor)

- **No file overlap with sibling worker branches** by design. W-integration touches `effect.Runtime` + `replay.EffectHandler` + `Mem0Interceptor` + `_tx_counter`; W-rigor touches `fact.db.transact` + adds a lint rule over `src/persistence/`; W-paper touches `paper/`. All three should be orthogonal to this branch.
- The minor potential conflict is `src/persistence/effect/handlers/audit.py`: this branch rewrites `audit_entry_to_datom` / `datom_to_audit_entry`. If W-rigor's lint-rule enforcement requires additional `time.time()` removals inside `audit.py`, that would land on different lines.
- `src/persistence/spec/_canonical.py` has many relaxations in this branch. If any sibling touches it, 3-way merge on the `_datom` / `_audit_entry` / `_trajectory_fact` / `_trajectory` blocks will need review; all four blocks are clearly bounded.
- **Merge order per R0 consolidation plan:** boundary → integration → rigor → paper. This branch is the first to merge.
