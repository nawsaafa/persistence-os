# W-polish3 Worker Summary

**Branch:** `W-polish3`
**Base:** `60b3c85` (main, post W-polish2)
**Scope:** ARIS Round 5 close-the-last-regression pass (R5 N1 MAJOR + R5 N2 MINOR + optional paper meta).

## Test-count evolution

| Checkpoint | Passing |
|---|---|
| Baseline on HEAD `60b3c85` | 575 |
| After Fix 1 (W6-factory-canonicalize) | **578** (+3 new tests) |
| After Fix 2 (W6-canonicalize-harmonize) | **579** (+1 new test) |
| After Fix 3 (W6-paper-meta) | **579** (docs-only) |

Final: **579 passed in 2.32s**.

## Fix 1 — W6-factory-canonicalize (closes R5 N1 MAJOR)

**Commit:** `f9d3900`

**Problem.** `make_audit_handler` at `src/persistence/effect/handlers/audit.py:364-381` hashed the *pre-canonical* `content` dict and then constructed the `AuditEntry` (which, since W-polish2, canonicalises `policy_id`/`handler_chain`/`principal` in `__post_init__`). `entry.id` thus hashed one shape but `verify_chain(entries)` re-hashed the canonical `entry.to_dict()` — mismatch, `False` on every chain whose `policy_id` arrived bare (the production shape from `policy_eval.py`).

**Fix.** Extracted `_canonicalise_content(dict) → dict` helper mirroring the dataclass rules on a plain dict (`policy_id` → `":" + lstrip(":")`; `handler_chain` → tuple of bare strings; `principal` keys → bare). Factory now calls `canonical_content = _canonicalise_content(content)` *before* `_content_hash` and passes the canonical dict to `AuditEntry(id=..., **canonical_content)` so the hash matches what `verify_chain` will recompute.

**Tests (new):** `tests/effect/test_audit_factory_verify_chain.py` (3 tests).
- `test_verify_chain_on_factory_with_bare_policy_id` — the R5 N1 reproducer; bare `policy_id="bankability-v3"` flows through `Runtime([raw, clock, audit])`, two `perform(":llm/call", …)` calls, assert `verify_chain(entries) is True`.
- `test_verify_chain_on_factory_with_bare_string_handler_chain` — pre-keyworded `handler_chain=(":audit", ":llm", ":tool")` in ctx; assert canonical bare form and verify.
- `test_verify_chain_on_factory_with_mixed_principal_keys` — mixed `principal={":user": "a", "session": "b"}`; assert both keys stripped to bare form and verify.

**Reproducer verification.** All 3 tests FAIL on HEAD `60b3c85` (confirmed pre-fix; see git log pre-commit output in session transcript). All 3 PASS after the fix.

## Fix 2 — W6-canonicalize-harmonize (closes R5 N2 MINOR)

**Commit:** `60d5dde`

**Problem.** `AuditEntry.__post_init__` used `prepend-if-missing` for `policy_id` (`if not startswith(":"): prepend`) but `lstrip(":")` for `handler_chain`/`principal` keys. The prepend-if-missing branch was non-idempotent on multi-colon inputs — `"::x"` stayed `"::x"`.

**Fix.** One-line harmonisation at `audit.py:126-137`: `":" + self.policy_id.lstrip(":")`. Idempotent on any number of leading colons; shape-matches sibling fields.

**Tests (new):** +1 test in `TestPolicyIdCanonicalizationAtInit` — `test_policy_id_canonicalisation_is_idempotent_on_double_colon` — covers `":x"`, `"x"`, `"::x"`, and `None` cases.

## Fix 3 — W6-paper-meta (optional, docs-only)

**Commit:** `a4046c2`

- `paper/persistence-nesy-2026-draft.md`: status bumped v0.2 → v0.3; revision-history entry for W-wire + W-polish2 + W-polish3 added; test-count citations updated **356 → 579** in abstract (L19), §4.7-adjacent (L37), §6 (L296), §6.6 (L362), and closing footer (L461).
- The `v0.1.0a1` tag is untouched (per scope — user's post-Round-6 action).

## Out of scope (per prompt)

- R2-G2/G3/G4 carried rigor items (deferred to pre-Phase-2 cleanup).
- Any Phase 2 module work.
- `DB.transact` input self-conform (F8 residual, Phase-2).
- Cutting the `v0.1.0a1` tag.

## Deviations

None. Two must-fixes + optional paper meta landed within the 30-minute budget. No tests disabled. No symbols renamed. No Phase-2 surface touched.

## Verification evidence

```
cd /Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-polish3
source /Users/nawfalsaadi/Projects/persistence-os/.venv/bin/activate
pytest -q
# → 579 passed in 2.32s
```
