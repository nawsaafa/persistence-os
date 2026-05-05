# persistence.claim CHANGELOG

## v0.9.0a1 (unreleased) — Phase 2.1c `persistence.claim` module

Phase 2.1c ships the `persistence.claim` package — the validation
and identity surface for structured context substrate endpoints.
It is a consumer-side module built on `persistence.sdk`; no raw
substrate imports cross the boundary.

### Added

- **`CLAIM_KINDS` frozenset** (`_registry.py`). Two registered kinds:
  `":claim/tool-exec"` and `":claim/blob-put"`. Includes a load-time
  drift-guard `assert len(CLAIM_KINDS) == len(_SCHEMAS)` (T2.1) that
  fires loudly if a kind is added to one mapping without a matching
  entry in the other.
- **`is_claim_kind(kind)` discriminator** (`_registry.py`). Returns
  `True` for members of `CLAIM_KINDS`; `False` for `:fact/*` namespace
  values and unknown strings. Used by HTTP routes to reject non-claim
  kinds at the API boundary before any substrate write.
- **`validate_attrs(kind, attrs)` total function** (`_validate.py`).
  Dispatches to per-kind Pydantic v2 schemas. Two schemas ship in
  2.1c: `ToolExecAttrs` and `BlobPutAttrs`. Cross-field invariant on
  `ToolExecAttrs`: `body_disposition ∈ {"inline", "blobbed", "discarded"}`
  determines whether `body_hash` is required (`blobbed`), forbidden
  (`inline`, `discarded`), or unconstrained. Violations raise
  `ClaimValidationError`. Unknown kind raises `UnknownClaimKindError`.
  Both are exported from the package root. The function is a total
  function — it never raises outside the two documented error types;
  Hypothesis `@max_examples=200` totality property verifies this
  (T3).
- **`CallerIdentity` + `CallerIdentity.attest` stub** (`_identity.py`).
  `attest()` returns `None` in Phase 2.1c. The stub acts as the
  2.1c.5 forward-compatibility seam: HTTP routes accept its `None`
  return without branching; when 2.1c.5 ships real Ed25519 verification,
  the return type widens without touching call-sites.
- **Public package API** (`__init__.py`). Re-exports:
  `CLAIM_KINDS`, `is_claim_kind`, `validate_attrs`,
  `ClaimValidationError`, `UnknownClaimKindError`, `CallerIdentity`.
- **Curated `s.claim.*` namespace** (`persistence.sdk`). Four names
  on the `Substrate` facade: `s.claim.kinds` (the frozenset),
  `s.claim.is_kind` (discriminator), `s.claim.validate` (validate_attrs),
  `s.claim.identity` (CallerIdentity class). All decorated
  `@experimental("v0.9.x")` — surface MOVABLE until `v0.9.0a1`.

### Stability

All public names carry `@experimental("v0.9.x")`. The shape of
`validate_attrs` and `CLAIM_KINDS` is movable until `v0.9.0a1`.

### Test surface

- `tests/claim/test_registry.py` (4 tests) — `CLAIM_KINDS` membership,
  `is_claim_kind` positive and negative paths including `:fact/*`
  rejection.
- `tests/claim/test_validate.py` (14 tests) — per-kind happy paths,
  missing required fields, oversize `body_summary`, `body_disposition`
  literal enforcement, cross-field `blobbed`/`inline`/`discarded`
  invariants, `UnknownClaimKindError` and `:fact/*` rejection.
- `tests/claim/test_props.py` (1 Hypothesis test) — `@max_examples=200`
  totality: `validate_attrs` raises only `ClaimValidationError` or
  `UnknownClaimKindError`, never an unchecked exception.
- `tests/claim/test_identity_stub.py` (2 tests) — `attest()` returns
  `None` for well-formed and boundary inputs.
- `tests/sdk/test_claim_namespace.py` (6 tests) — `s.claim.*` facade
  surface, `validate` raises on `:fact/*` kind, `identity` stub
  returns `None`, `persistence.claim` public API shape.

Total: **26 tests** (20 in `tests/claim/` + 6 in
`tests/sdk/test_claim_namespace.py`). Suite delta from
Phase 2.1b baseline (2215 passed): accumulated with Phase 2.1c
totals in the `feat/v0.9-2.1c-context-substrate` merge; see
`CHANGELOG.md` for the cumulative count.

### Forward references

- **Phase 2.1c.5** — `CallerIdentity.attest` real Ed25519 verification.
  Design ground truth (when written):
  `docs/plans/2026-05-04-phase-2.1c.5-caller-identity-design.md`.
  The `attest` stub and the `xfail(strict=True)` marker on
  `tests/http/test_auth.py::test_non_loopback_without_caller_signature_rejected_even_with_valid_bearer`
  are the falsifiable acceptance signals: both flip when 2.1c.5 lands.
- **Phase 2.1c.6** — substrate-side audit-chain wiring for fact-level
  transacts. Current `s.fact.transact` bypasses the canonical audit
  chain; 2.1c.6 closes this gap. Design ground truth (when written):
  `docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md`.
  The `xfail(strict=True)` marker on
  `tests/http/test_audit_chain.py::test_audit_chain_head_advances_with_emits`
  is the acceptance signal.

### ARIS gate

Design ARIS R2 PASS at mean **8.24 / min 7.6** (codex standard-mode,
two-round trajectory):
`docs/plans/2026-05-04-phase-2.1c-context-substrate-design.md`.

Impl ARIS R1.1 PASS at mean **8.22 / min 7.8** (codex high-mode):
five IMPORTANT findings folded clean across the R1.1 fix-pass
(`6ef4a67`).
