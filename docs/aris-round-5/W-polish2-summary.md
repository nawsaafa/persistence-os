# W-polish2 — ARIS Round 5 Surgical Polish Worker Summary

**Branch:** `W-polish2` (off `main` @ `61644f6`)
**Commits:** 5 (4 fixes + 1 bonus)
**Tests:** 551 → 575 (+24 net). Final `pytest -q`: `575 passed in 6.02s`.

---

## Fix-by-fix report

### 1. W5-audit-canonicalize — closes R1 N7 + R3 R4-N1 + R3 R4-N2

**Commit:** `d12946d`
**Files:** `src/persistence/effect/handlers/audit.py`, `tests/effect/test_audit_canonicalize.py` (NEW)
**Tests added:** 14 (10 failed before, 4 already passed — idempotency on bare/None inputs)
**Tests before → after:** 551 → 565.

`AuditEntry.__post_init__` now canonicalises three sibling wire-sensitive
fields at construction time, mirroring the `Datom.__post_init__` pattern
from W-wire:

- **`policy_id`** — prepends `":"` if missing. The `:audit/policy-id` spec
  slot requires keyword form; `policy_eval.py` emits bare strings
  (`"bankability-v3"`, `"unknown"`). Before W5, every production
  `AuditEntry.to_edn()` with a non-`None` policy raised `ValueError` at
  self-conform time (R1 N7 MAJOR).
- **`handler_chain`** — `lstrip(":")` on each entry to bare form.
  Before W5, pre-keyworded chains broke `verify_chain` after `from_edn`
  round-trip because the content hash was computed over keyworded form
  but `from_edn` stripped them back (R3 R4-N1).
- **`principal`** — `lstrip(":")` on each key in place (preserves dict
  identity). Same class as handler_chain, reachable via `from_edn`
  round-trip on pre-keyworded principals (R3 R4-N2).

`_principal_to_keyword_map` and `_handler_chain_to_keywords` lost their
"already colon" branch — with canonical internal form guaranteed, the
serialisation is a straight `":" + x`.

### 2. W5-datom-idempotent — closes R3 R4-N3

**Commit:** `a62c16f`
**Files:** `src/persistence/fact/datom.py`, `tests/fact/test_datom_idempotent.py` (NEW)
**Tests added:** 4 (all failed before). Tests: 565 → 569.

`Datom.__post_init__` used `a[1:]` to strip the leading colon. That
collapsed `":x"` correctly but left `"::x"` as `":x"` (still has a
colon — not idempotent under repeat construction). Switched both call
sites (`a` and `provenance["source"]`) to `lstrip(":")` so any number
of leading colons collapses uniformly. Guarded with a "changed?"
check before invoking `object.__setattr__` / dict mutation so the
happy path on bare-string input is unchanged.

### 3. W5-provenance-symmetry — closes R3 R4-N4

**Commit:** `1105811`
**Files:** `src/persistence/fact/wire.py`, `tests/fact/test_provenance_symmetry.py` (NEW)
**Tests added:** 5 (2 failed before, 3 already passed). Tests: 569 → 574.

`_provenance_to_wire` short-circuited on pre-keyworded keys, skipping
the value-keywordification branch. So `{":source": "bare"}` emitted
`{":source": "bare"}` — value leaked through unkeyworded. The
downstream datom self-conform caught it (loud, not silent), but the
function wasn't its own inverse on the pre-keyworded-key + bare-value
subdomain.

Fix: dropped the `continue`, always run the value-keywordification
branch. Key-normalisation now just picks which wire-key to use (bare
input → `":" + k` if known; already-keyworded input → verbatim); the
value-keywordification branch runs unconditionally for `:source`.

### 4. W5-paper-patch2 — closes R1 N8 + R4 research findings

**Commit:** `1e291b5`
**Files:** `paper/persistence-nesy-2026-draft.md`
**Changes:** 3 surgical edits, no code.

- **Date typo** — W-wire fixed §6 opener L296 (`2026-06-16` → `2026-06-09`)
  but missed bullet-4 L365 and the window reference on L317. All 3
  occurrences now read 2026-06-09. Final `grep`: 0 × `2026-06-16`,
  3 × `2026-06-09`.
- **Proposition 4 phantom** — Prop 4 referenced `append_audit_entry`
  which doesn't exist. The Merkle-chain emission lives in
  `make_audit_handler`'s clause closure. Rephrased to "produced by
  `make_audit_handler`'s Merkle-hashed chain-append clause" (names
  the real entry point; crisper than "produced by the audit handler's
  Merkle-chain emission").
- **§4.5 intervention drift** — old `I = ⟨step, field, new-value⟩`
  read as a single triple. Shipped replay engine and
  `:trajectory/intervention` slot both accept `seq_of`. Rewrote as
  "an intervention set `I = [⟨step, field, new-value⟩, ...]` — a
  (possibly empty) list of per-step modifications..." with a note
  that the single-triple case is the Phase-1 default so the
  subsequent replay operator definition (which uses `I.step` singular)
  still reads naturally.

### 5. R2 R4-G1 bonus — from_edn ∘ to_edn dataclass equality

**Commit:** `9299e40`
**Files:** `tests/effect/test_audit_from_edn.py`
**Tests added:** 1. Tests: 574 → 575.

With W5-audit-canonicalize landing canonical internal forms for
`policy_id`, `handler_chain`, and `principal` keys,
`from_edn ∘ to_edn` is now identity on the entire input domain
(modulo float `recorded_at` precision, which is exact for the test
fixture's chosen value). Added
`test_round_trip_equality_by_dataclass_eq` using the dataclass-
synthesised `__eq__` — stronger than the field-by-field check
because any new dataclass field automatically participates. Left
the granular per-field assertion in place for debug ergonomics.

---

## Verification

Targeted test files (per deliverable requirement 4):

```
pytest tests/effect/test_audit_self_conform.py \
       tests/effect/test_handler_chain_wire.py \
       tests/effect/test_audit_from_edn.py \
       tests/fact/test_wire_identity.py -v
# 26 passed in 0.22s
```

Full regression:

```
pytest -q
# 575 passed in 6.02s
```

---

## Commit log (chronological)

```
d12946d fix(effect): canonicalise AuditEntry sibling fields at __post_init__ (closes R1 N7 + R3 R4-N1/N2)
a62c16f fix(fact): Datom.__post_init__ idempotent on double-colon inputs (closes R3 R4-N3)
1105811 fix(fact): _provenance_to_wire keywordifies :source value uniformly (closes R3 R4-N4)
1e291b5 docs(paper): W5-paper-patch2 — date typo, Prop 4 phantom, §4.5 intervention drift
9299e40 test(effect): add dataclass-eq round-trip assertion for AuditEntry.from_edn (R2 R4-G1 bonus)
```

---

## Out-of-scope items honoured

- R2 R4-G1..G4 (beyond the one freebie taken) — Phase 2 rigor polish.
- N8 `DB.transact` + `mark_invalidated` split-transaction — Phase 2 STM co-design.
- Prop 1 scaling guard — Phase 2 benchmark work.
- `Runtime.assert_universal_audit` — camera-ready work.
- §6.3 bench walkthrough — camera-ready work.
- plan/txn/repl module stub changes — untouched.

No surprises. Target min ≥ 9.2 in Round 5 review.
