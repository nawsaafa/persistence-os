# ADR: `ctx_provider` extension to `make_audit_handler`

**Date:** 2026-04-22
**Status:** PROPOSED (design approved, implementation pending with Phase 3 of memory-palace-bitemporal-retrofit)
**Context design doc:** `docs/plans/2026-04-22-memory-palace-bitemporal-design.md` §4.6
**Raised by:** ARIS round 1 R1 N3 (convergent with R3 composability concerns)

## Context

`persistence.effect.handlers.audit.make_audit_handler` accepts `run_id`, `principal`, `policy_id`, `sink_name` at construction time and freezes them into the handler's `ctx`. Every `AuditEntry` produced by the handler inherits those frozen values, which is correct for handler-lifetime identifiers (e.g., "this whole session runs as principal X under policy Y").

The memory-palace bitemporal retrofit needs to attach `vault_snapshot_tx` to every `:vault/recall` audit entry. The value is **per-invocation** — each `/vault/recall` runs against a different `tx` snapshot, and the value must be captured at the moment the audit entry is written. Freezing it at handler construction would either pin a stale tx (useless) or require constructing a new handler per request (wasteful).

Three options considered.

## Options

### Option 1 — Add `vault_snapshot_tx` as a first-class `AuditEntry` field

Extend the `AuditEntry` dataclass with a new optional field; extend `_canonicalise_content` and `__post_init__` symmetrically.

**Pros:** self-documenting in the schema; easy to `verify_chain`.

**Cons:**
- Triggers an ARIS round on the schema change itself (the ARIS R3 arc around `_canonicalise_content` drift-pin cost 5 rounds of polish; breaking the canonicalisation byte-invariant is expensive to re-validate).
- Bakes vault-specific knowledge into `persistence.effect`, which so far knows nothing about vault semantics.
- Forces every non-vault caller to carry a nullable field they don't understand.

### Option 2 — Stuff `vault_snapshot_tx` into `principal`

Write `principal={"user_id": "...", "vault_snapshot_tx": 42}` and let the canonicalisation flow carry it.

**Pros:** zero schema change.

**Cons:**
- Semantically wrong. `principal` is about *who is making the call*. `vault_snapshot_tx` is about *what state they saw*. Conflating the two muddies audit queries ("show me all entries by principal X" would have to filter out snapshot noise).
- Breaks R5 canonicalisation invariants around `principal` (which already has a stable sort-key contract).
- Still construction-time, so doesn't solve the per-invocation variability.

### Option 3 — `ctx_provider` extension (CHOSEN)

Add an optional `ctx_provider: Callable[[], dict] | None = None` parameter to `make_audit_handler`. When present, it is evaluated inside the per-clause `with mask(audit_name):` block and its result dict is merged into `content` *before* `_canonicalise_content` runs.

```python
def make_audit_handler(
    entries: list[AuditEntry],
    *,
    wraps: Iterable[str] = (":llm/call",),
    sink_name: str | None = None,
    run_id: str | None = None,
    principal: dict[str, Any] | None = None,
    policy_id: str | None = None,
    ctx_provider: Callable[[], dict[str, Any]] | None = None,  # NEW
) -> Handler:
    ...
    # inside clause(), after building `content`:
    if ctx.get("_ctx_provider") is not None:
        extra = ctx["_ctx_provider"]()
        content = {**content, **extra}
    canonical_content = _canonicalise_content(content)
    ...
```

Vault wiring:

```python
vault = VaultFactStore(...)
audit_handler = make_audit_handler(
    entries,
    wraps={":vault/recall", ":vault/remember", ":llm/call"},
    principal={"user_id": caller_id},
    ctx_provider=lambda: {"vault_snapshot_tx": vault.current_tx()},
)
```

**Pros:**
- No schema change to `AuditEntry`.
- `_canonicalise_content` already preserves unknown keys (verified by R6 N1 drift-pin matrix at `tests/effect/test_audit_canonicalize_drift_pin.py` — "unknown-key preservation" case).
- `AuditEntry.__post_init__` mirrors dict-side canonicalisation for unknown keys via `object.__setattr__(self, k, v)` fall-through; the byte-invariant holds.
- Vault-specific knowledge stays in vault code; `persistence.effect` remains vault-agnostic.
- Composable: a future use case (e.g. `trace_span_id`) reuses the same hook with no new surface.

**Cons:**
- Slight performance cost (function call per audit entry). Measured in Phase 3 Task 3 benchmark; expected <1% overhead at audit-handler throughput.
- `ctx_provider` must not itself perform audited ops (infinite regress); guarded by the existing `mask(audit_name)` scope.

## Decision

**Option 3.** Ship `ctx_provider` as a new parameter on `make_audit_handler` with:

1. Full backward compatibility: omitting the parameter is a no-op (behaviour identical to current).
2. A new drift-pin test case in `tests/effect/test_audit_canonicalize_drift_pin.py` covering `ctx_provider`-injected fields — assert dict-side merge output is byte-identical to dataclass-side `__post_init__` output for common extras (string, int, dict of string→int).
3. A spec entry `:persistence.effect/audit-entry.vault-snapshot-tx` registered as optional in `persistence.spec`. Not required for the base handler; required for vault-wrapped handlers.

## Consequences

### Positive

- Unblocks Phase 3 of the memory-palace bitemporal retrofit without cross-cutting a breaking change.
- Establishes a clean pattern for future per-invocation audit metadata (trace IDs, HTTP request IDs, branch IDs).
- Keeps `persistence.effect` vault-agnostic.

### Negative / risks

- One more parameter on an already-8-parameter factory. Mitigated by keyword-only signature discipline.
- `ctx_provider` callers can inject arbitrary dict keys that collide with `content` (`prev_hash`, `op`, `args_hash`, ...). Mitigation: merge is `{**content, **extra}` so `extra` wins; Phase 3 lints reserved keys in the vault handler wrapper and rejects collisions at construction.
- If `ctx_provider` raises, the entire audit write fails — intentional; a broken ctx provider means the snapshot can't be captured, and a partial audit entry is worse than a loud failure.

## Implementation plan

Small PR to `persistence-os` (commits against `main`):

1. Test case first (TDD): new drift-pin matrix row for `ctx_provider` injection.
2. Test: `ctx_provider` is invoked once per clause dispatch, even if the clause re-enters (via `mask`).
3. Test: `ctx_provider` that raises surfaces as `AuditHandlerError`.
4. Implementation in `src/persistence/effect/handlers/audit.py` — ~10 lines inside `make_audit_handler`.
5. Spec registration in `src/persistence/spec/registry.py`.

Expected: ~30 minutes of engineering + 15 minutes of local TDD loop. Ships with Phase 3 Task 1 of the memory-palace bitemporal retrofit (~2026-05 kickoff).

## References

- `docs/plans/2026-04-22-memory-palace-bitemporal-design.md` §4.6
- `docs/aris-bitemporal-design-round-1/R1-correctness.md` finding N3
- `docs/aris-bitemporal-design-round-1/R0-consolidation.md` Class-A / R1 N3
- `src/persistence/effect/handlers/audit.py` `make_audit_handler` (lines 362–490)
- `tests/effect/test_audit_canonicalize_drift_pin.py` (29-case matrix — extension point for the new case)
