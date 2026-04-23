# persistence.plan CHANGELOG

## v0.1 (2026-04-28) — initial release

First release of the homoiconic plan AST module. Commits to three claims:

1. **Content-addressed Merkle DAGs.** Every `Node` carries a deterministic
   16-hex-char sha256 `:id` derived from canonical JSON form (sort_keys,
   no whitespace, pattern matches `persistence.replay._canonical`).
2. **Byte-identical round-trip.** `unparse(parse(x)) == x` for canonical inputs.
   Canonical form: sorted attrs keys, single-space separator, no extraneous whitespace.
3. **Spec validation.** Parse-time conformance against the registered
   `:persistence.plan/node` spec (shipped in `persistence.spec` Phase 1).
   Internal Node → external vector-form converter (`_to_vector_form`) injects
   computed `:id` ephemerally so `Node.attrs` stays uncluttered while the spec
   sees the canonical `[tag {:id ...} *children]` shape it validates.

### Public API

- `Node` — immutable dataclass (tag, attrs, children) with `.id` computed property
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

94 passed, 8 xfailed in `tests/plan/`:
- `test_ast.py` — Node construction, canonical form, :id (content-addressing)
- `test_parse.py` — parse, unparse, round-trip, spec validation, alias lowering
- `test_interpret.py` — walk order, visitor, unimplemented kinds
- `test_meta_target.py` — parse the track's own plan.edn (3 pass, 1 xfail)
- `test_misc.py` — unicode, deep nesting, edge cases (7 pass)

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
- `edn_format.Symbol` not handled in `_edn_to_python()` — bare EDN symbols
  like `->` pass through as opaque objects, breaking `Node.id` JSON
  serialization. Consumer-driven scope item for v0.2.
- Bare node shorthand `[:seq child1 child2]` (no attrs dict) rejected at
  parse time. Consumer-driven scope item for v0.2.
