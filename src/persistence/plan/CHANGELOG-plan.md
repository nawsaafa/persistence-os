# persistence.plan CHANGELOG

## v0.1 (2026-04-28) — initial release

First release of the homoiconic plan AST module. Commits to three claims:

1. **Content-addressed Merkle DAGs.** Every `Node` carries a deterministic
   32-hex-char (128-bit) sha256 `:id` derived from canonical JSON form
   (sort_keys, no whitespace, `allow_nan=False`, pattern matches
   `persistence.replay._canonical`). Birthday-collision probability 1%
   is reached at ~2.6×10^18 plans. Non-finite floats (NaN, Inf) are
   rejected at `:id` time to preserve reflexive equality. User-supplied
   `:id` in parsed EDN is stripped — the module computes `:id`, not the
   caller.
2. **Byte-identical round-trip.** `unparse(parse(x)) == x` for canonical inputs.
   Canonical form: sorted attrs keys, single-space separator, no extraneous whitespace.
3. **Spec validation.** Parse-time conformance against the registered
   `:persistence.plan/node` spec (shipped in `persistence.spec` Phase 1).
   Internal Node → external vector-form converter (`_to_vector_form`) injects
   computed `:id` ephemerally so `Node.attrs` stays uncluttered while the spec
   sees the canonical `[tag {:id ...} *children]` shape it validates.

### R2 fix-pass changes (pre-GA, no consumers yet)

- **Node.id widened 16 → 32 hex (64 → 128 bit).** Breaking vs R1 preview,
  but the module has not shipped a GA so no downstream depends on the
  narrower width. The `:persistence.spec/sha256` regex already accepts
  variable-length hex, so no spec change was needed.
- **NaN/Inf rejected in attrs.** `Node.id` on a Node containing non-finite
  floats raises `ValueError("non-finite float ...")` instead of silently
  producing a hash that compares non-equal to itself.
- **User `:id` stripped at parse time.** Parsing EDN with `{:id "x"}` no
  longer leaks the user string into `Node.attrs`; `:id` is always
  content-addressed. Closes two attack vectors (Canonical poisoning +
  spec-validation clobber via attrs.items() iteration in _to_vector_form).
- **Attr key shape validated.** `Node.attrs` keys must be plain strings
  without leading colon. `Node(attrs={':foo': ...})` or `{1: ...}` now
  raises at construction time.

### Public API

- `Node` — immutable dataclass (tag, attrs, children) with `.id` computed property
- `ID_HEX_WIDTH: int = 32` — module constant (use instead of hard-coding)
- `parse(edn_text, *, lower_aliases=None, strict=True)` — EDN text → Node
- `unparse(node)` — Node → canonical EDN text
- `walk(node, visitor=None)` — depth-first traversal, returns ordered `:id` list
- `ParseError` — malformed EDN shape
- `PlanSpecError` — wraps `ConformError` for spec validation failures
- `UnimplementedNodeKindError` — raised on `:code` / `:branch` leaves when walked

### Deferred (see design doc §3)

- Edit API (`read`/`splice`/`rewrite`/`compose`/`fork`/`promote`) → v0.2
- `:code` sandbox execution → v0.2
- Skill record storage → v0.3
- Pareto Vector Metric emission → v0.4
- Optimizers (MIPROv2 / MCTS / evolutionary) → Phase 3
- `:branch` speculative search → Phase 3
- Per-kind required-attr spec tightening → v0.2 (7 xfail tests pinning scope)

### Tests

151 passed, 7 xfailed in `tests/plan/` (776 passed, 7 xfailed full repo):
- `test_ast.py` — Node construction, canonical form, :id (content-addressing)
- `test_parse.py` — parse, unparse, round-trip, spec validation, alias lowering
- `test_interpret.py` — walk order, visitor, unimplemented kinds
- `test_meta_target.py` — parse the track's own plan.edn (3 pass, 1 xfail)
- `test_misc.py` — unicode, deep nesting, edge cases (7 pass)
- `test_property.py` — hypothesis property tests for claims 1 + 2 (R2 M4)

### Meta-target findings (test_meta_target.py)

The persistence-os-foundation track plan.edn was exercised as the meta-target.
Partial parse succeeded; two v0.2 scope items block the full walk:

1. **Bare `:seq` shorthand** — `[:seq [:tool-call ...]]` without an attrs dict at
   position 1. The v0.1 parser enforces `[tag, dict, *children]` per spec.
   Fix: `_python_to_node()` should inject `{}` when position 1 is a vector.

2. **`edn_format.Symbol` not JSON-serializable** — `->` and similar symbols in
   `:signature` attr values (from EDN `'[datom-schema -> interceptor-py]`) are
   `edn_format.Symbol` objects. `json.dumps` raises `TypeError` in `Node.id`.
   Fix: `_edn_to_python()` should convert `Symbol` to `str(symbol)`.

EDN quote reader macro (`'[...]`) handled by `_sanitize_edn_quotes()` workaround
(strip leading `'` before parse). 4 quote-macros found in track plan, 0 remaining
after sanitization. Track plan `:track/plan` vector: 8405 chars, bracket-balanced.

### Dependencies

- `persistence.spec` (registered `:persistence.plan/node`)
- `edn_format >= 0.7.5` (PyPI)

### Known v0.1 limitations (see ARIS R2/R3 inputs)

- `:persistence.plan/node` spec is lenient on per-kind required attrs
  (e.g., `:tool-call` without `:tool` passes). Tightening is a v0.2
  spec extension; 7 xfail tests pin the intended behavior.
- Alias lowering (`:phase` → `:seq`) is lossy for round-trip by design.
- Walker is pure depth-first; no parallelism for `:par`, no MCTS for
  `:branch`, no unrolling for `:loop`. Executor semantics land in later
  phases.
- `edn_format.Symbol` handled by `_edn_to_python()` via ``str(symbol)``
  coercion (R2 C4). Symbols like ``->`` now survive through Node.id and
  unparse paths. Previously a consumer-driven v0.2 scope item; shipped
  in v0.1 so the meta-target can parse end-to-end.
- Bare node shorthand `[:tag child1 child2]` (no attrs dict at position 1)
  now accepted (R2 C4). `_python_to_node()` injects `{}` when position 1
  is a list and treats everything from index 1 onward as children.
  Previously rejected with "attrs must be map".

## Schema evolution & id stability (contract)

`Node.id` is pinned to: `sha256(canonical-json(_canonical_dict(node)))[:ID_HEX_WIDTH]`
— where `ID_HEX_WIDTH = 32` (see `_ast.py:ID_HEX_WIDTH`) and the canonical
JSON uses `sort_keys=True`, `separators=(",", ":")`, `allow_nan=False`.
Anything that changes those inputs changes every persisted id.

**Breaking changes** — would move `Node.id` for the same logical node.
Downstream stores that key off `Node.id` must re-hash:

- Attr-name rename (e.g. `prompt` → `message`). `_canonical_dict` serializes
  attrs verbatim; a rename rehashes.
- Any change to `_canonical_dict` serialization — field ordering, type
  coercion, null handling, nested-Node handling in attrs.
- Hash algorithm swap (sha256 → blake3) or width change (e.g. 32 → 40 hex).
- Adding a new structural attr that participates in canonical form —
  `original-tag` (R3-M3) is a recent example: it IS canonicalized because
  it sits in `node.attrs`, so enabling alias lowering on a pre-existing
  plan regenerates ids for the aliased nodes only.
- Alias-lowering policy change (toggling the `original-tag` injection
  on/off, changing which aliases are lowered at read time).

**Preserving changes** — safe, will NOT move `Node.id`:

- Visitor / walker logic (`_interpret.py`).
- Error-message text on `ParseError` / `PlanSpecError`.
- Parser whitespace / comment handling (EDN input-side only).
- CHANGELOG / docstring edits.
- New attr names that are reserved (`:id`, any future `:meta-*` family)
  and explicitly stripped before `_canonical_dict` — see the
  `{"id", ":id"}` strip in `_python_to_node`.

**Version contract for external pinners.** Callers that persist `Node.id`
should pin `persistence.plan ~= 0.2` (or `~= 0.Y` for whatever minor
they first tested against). Minor and patch bumps within the same
major preserve ids; a major bump (`0.x` → `1.0`, or `1.x` → `2.0`)
signals an id-space break.

**Future breaking-change plan.** If a canonical-form change becomes
unavoidable, the migration path is to add a parallel id namespace
(`:id@v2` alongside `:id@v1` in the spec) plus a migration helper
`recompute_ids(node, target="v1" | "v2")`. This keeps old persisted
ids queryable while letting new writes adopt the new form.
