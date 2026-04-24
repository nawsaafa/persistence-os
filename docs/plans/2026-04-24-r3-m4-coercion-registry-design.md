# R3-M4 — `Node.id` Coercion Registry (Design Only)

**Status:** design draft, deferred from v0.2.0a1 ARIS gate, held through the
v0.2.0a2 hardening batch. Implementation target: **v0.3.0a1** (scope cut in §9).
**No code in this document.** The goal is to unblock a later batch by settling
the semantic choices now, not to ship.

Related contract: `src/persistence/plan/CHANGELOG-plan.md` → "Schema evolution
& id stability (contract)". Every decision below is checked against that
contract; divergences are called out explicitly.

## 1. Motivation

`Node.id` is `sha256(json.dumps(_canonical_dict(node), sort_keys=True,
allow_nan=False))[:32]` (see `src/persistence/plan/_ast.py:90-126`).
`json.dumps` only serializes `dict / list / tuple / str / int / float / bool /
None`. Real plan authors — the skill-library, ModelForge parameter blocks, the
meta-target track plan — already want `bytes`, `datetime`, `Decimal`, `UUID`,
`frozenset`, and domain objects inside attrs. Today these raise `TypeError` at
id-time. The v0.1 parser already papers over one case: `_edn_to_python`
stringifies `edn_format.Symbol` (`src/persistence/plan/_parse.py:75-78`) because
EDN symbols like `->` in `:signature` broke the meta-target. That special case
is the seed of a general mechanism; R3-M4 promotes it to a sanctioned registry
so the next six types don't each require a parser patch.

## 2. Non-goals

The coercion registry is explicitly NOT:

- **A type-system for attrs.** Attrs stay duck-typed `Mapping[str, Any]`.
  Registration adds a canonicalization path; it does not constrain what can be
  stored.
- **A serializer for emit/unparse.** `_emit_value` owns EDN emission and keeps
  its own type dispatch (it has different constraints — readable round-trip vs.
  byte-deterministic hash input).
- **A mechanism for preserving Python identity across replay.** Content-address
  is on canonical bytes, not on Python objects. Two hosts with `bytes(b"abc")`
  vs. the hex string `"616263"` in attrs will hash identically if the coercion
  of `bytes` is `.hex()` — and that is the intended invariant.
- **A late-binding escape hatch that varies per-process.** If the registry
  state is allowed to drift between processes, the content-addressing claim
  (Claim 1, paper §4) dies silently. Determinism is load-bearing.

## 3. API surface

```python
from persistence.plan import register_coercion, unregister_coercion, Coercion

@register_coercion(datetime)
def _coerce_datetime(dt: datetime) -> str:
    return dt.isoformat()

# Lookup (internal; exposed for debugging only)
fn: Coercion | None = lookup_coercion(datetime)

# Unregistration — tests only. Raises in non-test contexts (see §6).
unregister_coercion(datetime)
```

- `Coercion` is the type alias `Callable[[Any], JSON]` where `JSON` is any
  value `json.dumps` already accepts.
- Registration is **module-level, import-time, one-shot per type**. A second
  registration for the same type raises `ValueError` unless `replace=True` is
  passed (reserved for test harnesses).
- Thread-safety: the registry is an `immutables.Map` (or an `MappingProxyType`
  rebuilt on every write). No RMW sequence is exposed. Readers are lock-free.
- Discovery: `_canonical_dict` grows a walker that, for each attr value that
  `json.dumps` would reject, looks up `type(value).__mro__` in the registry
  (exact-type match first, then walk MRO for subclass-of registered). Miss →
  error path per §4 Q3.

## 4. Semantics decision matrix

| Q   | Question                                     | Choice                                | Rationale |
| --- | -------------------------------------------- | ------------------------------------- | --------- |
| Q1  | Pre-coerce at Node construction vs. id-time  | **Id-time (transient)**               | Pre-coerce surprises users who read back `node.attrs["valid_at"]` and get a string instead of their `datetime`. Id-time keeps attrs faithful to author intent; canonicalization is invisible. Cost: `node.attrs` and `_canonical_dict(node)` can legitimately disagree on types — documented, not a bug. |
| Q2  | Hash registry version into `Node.id`         | **No**                                | Yes = every default-registry tweak breaks every persisted id — catastrophic coupling. No = two hosts with divergent registries compute different ids for the same Node — correctness hole. We close the hole via §6 (static registry, no runtime registration in production) rather than via hash-in. |
| Q3  | Unregistered types: strict vs. lenient       | **Strict (raises `TypeError`)**       | The contract says "canonical or nothing". A `repr()` fallback silently encodes object memory addresses into ids — the exact failure mode content-addressing exists to prevent. Strict error points the author at `register_coercion`. |
| Q4  | Defaults to ship                             | **See list below**                    | Each default is justified on cross-host determinism grounds. |
| Q5  | Does coercion affect `Node` equality         | **Yes, via id-equality**              | `Node.__eq__` remains dataclass-generated (tag + attrs + children); `Node.id` equality remains the content-address check. Since Q1 = id-time, two Nodes with `attrs={"t": datetime(...)}` and `attrs={"t": "2026-04-24T..."}` have **equal ids but unequal attrs** — and therefore unequal Nodes by dataclass `__eq__`. Document this asymmetry prominently; it is the price of Q1. |

### Q4 defaults

| Type                | Coercion             | Why |
| ------------------- | -------------------- | --- |
| `datetime.datetime` | `.isoformat()`       | ISO-8601 is unambiguous, timezone-aware, sortable. `.timestamp()` loses TZ + precision. |
| `datetime.date`     | `.isoformat()`       | Same argument, narrower type. |
| `bytes`             | `.hex()`             | Deterministic, case-stable. `base64` is valid but mixes case; `str(bytes)` is `__repr__` and truncates. |
| `decimal.Decimal`   | `str(d)`             | Preserves precision + scientific notation author wrote. `float(d)` loses precision silently (the whole point of `Decimal`). |
| `uuid.UUID`         | `str(u)`             | Canonical `xxxxxxxx-xxxx-...` form is lowercase-stable across platforms. |
| `frozenset`         | `sorted(list(fs))`   | Insertion order is non-deterministic; sorting the elements (by their `json.dumps` canonical form) fixes order. Requires all elements be individually coercible — recurse. |
| `edn_format.Symbol` | `str(sym)`           | Absorbs the existing `_edn_to_python` special case and removes the one-off from `_parse.py:75-78`. |

Explicitly NOT in defaults: `pydantic.BaseModel`, dataclass instances, NumPy
scalars. These have non-stable canonical forms across library versions and
should be registered by the consuming app.

## 6. Cross-host determinism contract

The registry must hash identically on every host that computes `Node.id` for
the same logical tree, or Claim 1 (Merkle DAG) dies.

Three options were considered:

- **Static (no runtime registration in production).** The default registry is
  populated at module import time from a frozen table (the §4 Q4 list).
  `register_coercion` is callable only under a test sentinel (e.g.
  `PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION=1` or a pytest fixture).
  Production code paths never mutate the registry. Determinism: guaranteed
  by import-time immutability plus the version-pin below.
- **Versioned.** Every registration bumps a `REGISTRY_VERSION` integer that
  gets folded into the canonical form. Two hosts with different registries
  compute different ids — but ANY registry change also invalidates every
  previously persisted id. Rejected: forces a flag day on every registration.
- **Manifest.** A module-level constant `PLAN_CANONICAL_VERSION` (analogous to
  `ID_HEX_WIDTH`) is bumped only when the default registry changes in a
  canonical-form-altering way. Runtime registration is forbidden (as in
  static). Consumers pin against `PLAN_CANONICAL_VERSION` in their storage
  layer. Breaking changes are explicit, visible, and rare.

**Choice: Static + Manifest.** Static blocks the footgun (two hosts diverging
on runtime registrations). Manifest documents the one remaining class of
break (we changed `datetime` coercion from `.isoformat()` to `.timestamp()`,
or added a new default that now catches a previously-errored type). This
composes cleanly with the existing CHANGELOG contract: a `PLAN_CANONICAL_VERSION`
bump is co-incident with a major-version bump of `persistence.plan`.

## 7. Migration story

If v0.3.0a1 shipped with default `datetime → .isoformat()` and v0.5 needed to
change it (e.g. to nanosecond-precision ISO), pinned `:id`s from v0.3 become
incomputable under v0.5's registry. Three options:

- **Pin and break.** `PLAN_CANONICAL_VERSION` bump, document in CHANGELOG,
  consumers re-hash. Simple; hostile to ops.
- **Parallel registries.** `register_coercion(datetime, fn, version=2)`
  stores both the v1 and v2 coercion. `Node.id` takes an optional
  `version=` kwarg (default: current); callers that stored v1 ids compute
  them via `node.id(version=1)` during migration. More code, more surface,
  but survivable by long-lived stores.
- **`recompute_ids(tree, target_version)` helper.** A migration-time walker
  that rehashes a persisted tree under a target registry version. Combines
  with parallel registries: you hold both old and new ids during a dual-write
  window, then cut over.

**Choice: parallel registries + `recompute_ids` helper.** This is the same
shape as the CHANGELOG "Future breaking-change plan" (`:id@v1`/`:id@v2` plus
`recompute_ids`) — R3-M4 inherits that machinery rather than building a
competing one. Acknowledged trade-off: the coercion-registry version namespace
MUST be the same namespace as the schema-evolution version namespace (both
are `PLAN_CANONICAL_VERSION`). Two separate version counters would ship us
into combinatorial-explosion hell.

**Deferral:** parallel registries are **not** in v0.3.0a1 scope (see §9). v0.3
ships with static + manifest only; parallel-registries land in v0.4+ when the
first default actually needs to change.

## 8. Test plan

The implementation that lands R3-M4 must add tests covering:

1. `datetime` attr value: `Node.id` computes; no `TypeError`.
2. `bytes` attr value: `Node.id` computes; `bytes(b"abc").hex() == "616263"`
   produces the same id as `attrs={"blob": "616263"}`.
3. `Decimal("1.23")` and `Decimal("1.2300")` produce **different** ids
   (precision-preserving — the whole point of `Decimal`).
4. `frozenset({3, 1, 2})` and `frozenset({1, 2, 3})` produce the **same** id
   (sorted canonicalization).
5. Unregistered type (e.g. a bare `object()`): `Node.id` raises `TypeError`
   with a message mentioning the type name and pointing at `register_coercion`.
6. `edn_format.Symbol` through the parse path: `parse(...).id` succeeds, and
   `_edn_to_python` no longer carries the special-case stringification (one-
   off absorbed into the registry).
7. Registry snapshot test: given the v0.3 default registry, a canonical
   fixture tree hashes to a pinned id string (guards against accidental
   reorderings).
8. Cross-host determinism proxy: import the module twice in a subprocess,
   register a custom coercion in process A only, confirm process B's id for
   the same tree with that custom type raises `TypeError` — AND confirm
   production mode (no sentinel env var) rejects `register_coercion` calls.
9. Q5 asymmetry: construct two Nodes with `attrs={"t": <datetime>}` and
   `attrs={"t": "<iso-string>"}`. Assert `a.id == b.id` AND `a != b`.
   Document this as the intended Q1 consequence.
10. MRO lookup: register on `datetime.date`; pass a `datetime.datetime` (subclass);
    confirm it uses the registered `date` coercion via MRO walk.

## 9. Scope cut for v0.3.0a1

This design doc does NOT commit to all nine questions landing in v0.3.0a1.
Minimum shippable subset:

- **Q1:** id-time coercion. ✅ ship.
- **Q2:** no version-in-hash. ✅ ship (static-only closes the hole).
- **Q3:** strict error on unregistered. ✅ ship.
- **Q4:** seven defaults (see §4 Q4 table). ✅ ship.
- **Q5:** id-equality semantics documented; `Node.__eq__` unchanged. ✅ ship.
- **§6:** static + manifest (no runtime registration outside tests;
  `PLAN_CANONICAL_VERSION = 1` introduced). ✅ ship.
- **§7:** parallel registries + `recompute_ids`. **DEFERRED to v0.4+.** v0.3
  ships with break-on-change as the only migration path. This is acceptable
  because there are no consumers persisting ids yet.
- **§8:** 10 tests. ✅ ship.

## 10. Dependencies + coupling

- **`persistence.fact`.** `fact/db.py:70` uses 16-hex (64-bit) sha256 truncation
  for `_hash_fact`. `persistence.plan` uses 32-hex (128-bit) via `ID_HEX_WIDTH`.
  These are DIFFERENT content-addressing schemes with different collision
  budgets; they serve different layers (datom log vs. plan AST). R3-M4 does
  NOT couple them. A future substrate-wide id-width unification is a separate
  track, deliberately scoped out here. **Choice: separate.**
- **`persistence.replay`.** `_canonical` in replay shares the `json.dumps(...,
  sort_keys=True, separators=(",", ":"))` pattern with `Node.id`. If replay
  gains its own coercion needs, it should import from
  `persistence.plan._canonical` rather than reimplementing. Flagged as a
  follow-up; not in v0.3.0a1 scope.
- **`persistence.spec`.** No coupling. Spec validates vector form which runs
  after `Node.id` is computed. Registry misses surface as `TypeError` from
  `Node.id`, not as spec violations.
- **`edn_format`.** Removing the `_edn_to_python` special case for `Symbol`
  (§4 Q4, §8 test 6) means v0.3's parser relies on the coercion registry
  being pre-populated at import time. Import-order fragility is the cost;
  a module-level registration at the bottom of `_ast.py` (or an explicit
  `_defaults.py` imported by `__init__`) pays it.
