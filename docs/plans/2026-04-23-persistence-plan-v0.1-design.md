# persistence.plan v0.1 — Design Spec

> **Versioning note:** "v0.1" refers to the module version. Ships in the persistence-os package release `v0.2.0a1` (first additive release after Phase 1 freeze at `v0.1.0a1`).

**Status:** approved 2026-04-23 · ready for writing-plans
**Track:** `ai-box/conductor/tracks/persistence-os-foundation_20260420/` Phase 2.B
**Author:** Nawfal Saadi · brainstormed with Claude
**Target ship:** 2026-04-28 (3d impl + 2d ARIS)
**Paper target:** NeSy 2026 (abstract 2026-06-09, paper 2026-06-16)

---

## 1. Goal

Ship the smallest `persistence.plan` module that is committable, ARIS-reviewable, and warrants one paper-citable proposition. No more.

## 2. The commitment (three claims)

v0.1 commits to exactly three behaviors, each provable by tests:

1. **Plans are content-addressed Merkle DAGs.** Every `Node` carries a deterministic `sha256`-derived `:id`. Independently-constructed identical plans hash-collide.
2. **Parse round-trips byte-identical.** `unparse(parse(edn_text)) == edn_text` for all canonical inputs.
3. **Spec validation catches malformed plans.** Invalid plans raise `ConformError` against the `:persistence.plan/node` spec (already registered in `persistence.spec` Phase 1).

Anything beyond these three claims is out of scope for v0.1.

## 3. Explicitly out of scope (with v0.x upgrade paths)

| Feature | v0.x | Why deferred |
|---|---|---|
| Edit API (read/splice/rewrite/compose/fork/promote) | v0.2 | YAGNI until real plan rewriting is needed (Trader v2 post-trade analysis) |
| Skill record storage + versioning | v0.3 | Requires `persistence.fact` integration; no active consumer yet |
| 4-gate automated skill promotion | v0.4 | Research claim requiring real usage data before warranting |
| Pareto Vector Metric emission | v0.4 | No optimizer exists to consume it; clean addition, not retrofit |
| MIPROv2 / MCTS / evolutionary optimizers | Phase 3 (v0.5+) | Plan.edn explicitly scopes these to Phase 3 |
| `:code` sandbox execution | v0.2 | Requires e2b or docker exec harness; v0.1 raises `UnimplementedNodeKindError` |
| `:branch` speculative search | Phase 3 | Requires MCTS outer loop |
| Real `:llm-call` / `:tool-call` executors | v0.2 | v0.1 uses no-op executors; real dispatch lands when `persistence.effect` wiring is needed |

**Core principle:** retrofitting additions is almost always cheaper than retrofitting reversals. v0.1 adds nothing it cannot honestly warrant.

## 4. Context

### 4.1 Prior art already shipped

- **`persistence.spec` v0.1.0a1** (Phase 1) — already registers `:persistence.plan/node` with 16 node kinds (`:seq`, `:par`, `:choice`, `:case`, `:loop`, `:race`, `:let`, `:branch`, `:ref`, `:tool-call`, `:llm-call`, `:code`, `:checkpoint`, `:reflect`, `:verify`, `:call-skill`). v0.1 consumes this spec; it does not define it.
- **`persistence.fact`, `.effect`, `.replay`** (Phase 1) — shipped but not wired into `plan` v0.1. Wiring lands in v0.2+ when real executors replace the v0.1 no-ops.
- **Clojure prototype** at `docs/agent2-plan-spec.md` §8 (94 lines) — reference implementation for the interpreter. v0.1 ports the `node`, `rewrite`, and `evaluate` primitives; skips mutation operators + evolutionary outer loop (those are v0.4).

### 4.2 The meta-target

The track's own `plan.edn` (at `ai-box/conductor/tracks/persistence-os-foundation_20260420/plan.edn`) uses `:phase` and `:workstream` wrappers that are NOT in the `:persistence.plan/node` spec. v0.1 handles this via optional alias lowering at parse time (see §6.2 below), not by extending the spec. The spec remains authoritative.

## 5. Architecture

```
src/persistence/plan/
├── __init__.py      # public API: parse, unparse, walk, Node, errors
├── _ast.py          # Node dataclass; canonical form; sha256 :id
├── _parse.py        # EDN ↔ AST; validates against :persistence.plan/node
└── _interpret.py    # walk() — depth-first no-op traversal emitting :id trace
```

**Dependencies:** `persistence.spec` only. No `persistence.fact`, no `persistence.effect`, no `persistence.replay` in v0.1. Dependency additions land in v0.2+ when real executors are wired.

**Rationale:** keeping the dependency surface minimal lets `persistence.plan` v0.1 be tested in isolation, ARIS-reviewed in isolation, and cited in the paper as a clean addition to the substrate. Every other persistence module also depends on `persistence.spec`; this is the substrate's central coordination point.

## 6. Components

### 6.1 `Node` (in `_ast.py`)

Immutable dataclass representing an AST node:

```python
@dataclass(frozen=True, slots=True)
class Node:
    tag: str               # e.g. ":seq", ":llm-call"
    attrs: Mapping[str, Any]  # frozen dict; keyword keys per spec
    children: tuple[Node, ...]

    @property
    def id(self) -> str:
        """sha256(canonical_form)[:16] as hex string."""
        ...
```

**Canonical form** for `:id` computation matches the Phase 1 pattern established by `persistence.effect` (`_canonicalise_content`) and `persistence.fact` (`Datom.__post_init__`):
- Attrs emitted in sorted-key order
- Strings keyword-normalized (leading colon preserved; no literal dots)
- Children emitted in document order
- No whitespace, no quotes beyond spec
- Deterministic across processes + Python versions

**Rationale:** identical pattern to effect/fact/replay canonicalisers means one shared invariant (`parallel-canonicaliser drift`) gets caught by the existing R6 N1 drift-pin test pattern when we extend it to include `plan`. Consistency with Phase 1 reduces review surface.

### 6.2 `parse` + `unparse` (in `_parse.py`)

```python
def parse(
    edn_text: str,
    *,
    lower_aliases: Mapping[str, str] | None = None,
    strict: bool = True,
) -> Node:
    """Parse EDN text to Node. Validates against :persistence.plan/node.

    lower_aliases: optional {":phase": ":seq", ":workstream": ":seq"} for
    reading the track's own plan.edn without polluting the spec.
    """

def unparse(node: Node) -> str:
    """Node → EDN. Round-trip invariant: unparse(parse(x)) == x byte-identical."""
```

**Alias lowering design decision:**
- Aliases are **applied at read time, not stored in the Node tree**. A `:phase` alias becomes a `:seq` Node with no trace of the original tag.
- This keeps the spec authoritative: `parse()` always returns spec-conformant Nodes.
- `unparse()` of an alias-lowered parse does NOT recover the original `:phase` wrapper — this is acceptable because aliases are a READING convenience, not a lossless mapping.
- Round-trip byte-identity therefore only holds for inputs that do NOT use aliases. This is documented and tested explicitly.

### 6.3 `walk` (in `_interpret.py`)

```python
def walk(node: Node, visitor: Callable[[Node, tuple[str, ...]], None] | None = None) -> list[str]:
    """Depth-first traversal. Returns ordered list of :ids visited.

    visitor(node, path): optional callback per node. `path` is the tag breadcrumb.
    No side effects. No executors. Pure traversal emission.
    """
```

**Walk semantics for v0.1:**
- Depth-first, parent-before-children for ordered containers (`:seq`, `:let`)
- `:par` children walked in **document order** (v0.1 does not actually parallelize — this is a deliberate non-claim, documented)
- `:choice` walks ALL `:case` branches (not selector-dispatched — v0.1 is pre-execution analysis, not runtime)
- `:loop`, `:race`, `:branch` walk the body once (unrolling is executor concern, not walker concern)
- `:code`, `:branch` in leaf position raise `UnimplementedNodeKindError` with explicit v0.x upgrade message
- Every other leaf (`:tool-call`, `:llm-call`, `:checkpoint`, `:reflect`, `:verify`, `:call-skill`, `:ref`) is visited and added to the `:id` trace; no execution

**Paper honesty:** v0.1 explicitly does NOT claim "parallel execution" or "optimized dispatch." Walk is pre-execution static analysis emitting structural traces. The paper warrant is content-addressing + round-trip + spec validation — nothing more.

## 7. Data flow

```
EDN text ──parse()──▶ Node tree (spec-validated) ──walk()──▶ ordered :id trace
                            │
                            └─── unparse() ──▶ EDN text (byte-identical to input)
```

No checkpoint writes, no effect dispatch, no skill resolution, no Pareto emission. Every integration point is deliberately absent in v0.1 and present as an explicit v0.x upgrade.

## 8. Error handling

All errors fail-closed with explicit, actionable messages. No silent fallthrough, no best-effort.

| Error | Raised by | Trigger |
|---|---|---|
| `ParseError` | `_parse.py` | Malformed EDN syntax. Message includes source position + excerpt. |
| `ConformError` | `persistence.spec` (reused) | AST fails `:persistence.plan/node` validation. Reuses Phase 1 error type for consistency. |
| `UnimplementedNodeKindError` | `_interpret.py` | Walker hits `:code` or `:branch` in leaf position. Message names the v0.x that ships real support. |

**Explicit non-errors:**
- Alias lowering is silent when `lower_aliases` is provided (documented behavior, not a warning)
- Empty `:seq` / `:par` / `:let` are valid (empty-children is a legitimate degenerate case)
- Deeply-nested plans (≥ 100 levels) parse successfully (no arbitrary depth limit in v0.1)

## 9. Testing strategy (TDD per Phase 1 precedent)

Target: ~200 tests, modeled on `persistence.fact` / `persistence.effect` invariant-pinning style.

| Category | Count | Pinned invariants |
|---|---:|---|
| Parse round-trip | 30 | 5 canonical shapes × full + sub-tree byte-identity; alias lowering explicitly NOT round-trip; empty-children edge cases |
| Content-addressing | 25 | Identical EDN → identical `:id` across processes; any attr change → different `:id`; child-order change → different `:id`; child-order stability for ordered containers |
| Spec conformance | 40 | Each of 14 v0.1-supported node kinds: 1 valid + 2 malformed (missing required attr, wrong kind, unknown child type). `:code` + `:branch` tested separately (parse OK, walk raises). |
| Walk order | 25 | `:seq` child order; `:par` document order; `:let` binding scope; `:choice` walks all cases; `:loop`/`:race` body-once; deeply-nested DFS |
| Unimplemented | 10 | `:code` + `:branch` each raise with message naming v0.2 (code) / Phase 3 (branch) |
| Track integration (meta-target) | 5 | Parse `persistence-os-foundation_20260420/plan.edn` with alias lowering; assert spec-conformance of lowered AST; walk emits 30+ ordered `:id`s |
| Edge cases + misc | 65 | Unicode in prompts; empty tree; single-node tree; attribute value types (str/int/bool/keyword/nested dict); cross-process determinism via subprocess |

**Meta-target test** (the track's thesis):

```python
def test_meta_parse_track_plan_edn():
    """The plan.edn file of THIS track must parse, validate, and walk."""
    track_edn = Path("ai-box/conductor/tracks/persistence-os-foundation_20260420/plan.edn").read_text()
    node = parse(track_edn, lower_aliases={":phase": ":seq", ":workstream": ":seq"})
    ids = walk(node)
    assert len(ids) >= 30
    assert len(set(ids)) == len(ids)  # all :ids unique (content-addressing works across real plan)
```

When this test passes, v0.1 has honored the track's opening meta-note: *"when plan/eval can execute this file, Phase 3 ships by definition."* Not literally Phase 3, but the first step of it.

## 10. Paper warrant (Proposition candidate)

v0.1 warrants one claim for the paper:

> **Proposition Pn (candidate):** *Plans in `persistence.plan` are content-addressed Merkle DAGs. Two agents that independently construct identical plan fragments hash-collide and share storage.*

**Evidence:** 30 round-trip tests + 25 content-addressing tests + 2-process determinism test + canonical-form equivalence with Phase 1's `effect` / `fact` canonicalisers.

This is a minor addition to the paper's thesis, not an over-claim. The warrant is by test, not by hand-wave.

## 11. Decision record

| Decision | Chosen | Alternatives considered | Reason |
|---|---|---|---|
| v0.1 scope | Minimal (parse + validate + walk + content-address) | B+ (add edit API), B++ (add Pareto hooks), C (add skill storage), D (full optimizer) | YAGNI. Each addition has a named v0.x. "Smallest committable unit" framing (see auto-memory `feedback_two_games_ship_vs_research.md`). |
| Alias lowering | Parser kwarg, lowered at read time | Extend spec with `:phase`/`:workstream`; reject track plan.edn | Keeps spec authoritative; meta-target works without spec pollution; aliases are a reading convenience |
| No `persistence.fact`/`.effect`/`.replay` wiring | Deferred to v0.2+ | Wire in v0.1 for completeness | No active consumer; wiring forces design commitments v0.1 shouldn't yet make |
| Canonical form pattern | Match Phase 1's `_canonicalise_content` | Custom canonical form | Consistency reduces review surface; R6 drift-pin test pattern extends naturally |
| Walk emits `:id` trace | Pure traversal, no execution | Walk dispatches to executors | v0.1 is pre-execution static analysis; execution is v0.2+ concern |
| ARIS reviewers | R1 + R2 + R3 (skip R4) | Full 4-reviewer | No external-system wiring in v0.1 → R4-mandatory rule does not apply |

## 12. Timeline + ARIS plan

| Day | Activity |
|---|---|
| 2026-04-24 | Implementation TDD: `_ast.py` (Node + canonical form + `:id`) + `_parse.py` (EDN parse/unparse + spec validate + alias lowering) |
| 2026-04-25 | Implementation TDD: `_interpret.py` (walk + error handling) + `__init__.py` (public API) + meta-target test |
| 2026-04-26 | Full suite green (~200 tests); manual smoke against track plan.edn; commit on feat branch |
| 2026-04-27 | ARIS Round 1 — 3 reviewers (R1 correctness, R2 rigor, R3 composability). Skip R4 per §11. |
| 2026-04-28 | Fix-pass if needed; Round 2 if scores below 8.5 min; ship to main, tag `v0.2.0a1` on persistence-os repo |

**Gate:** ARIS Round 1 min ≥ 7.0 (gate target per track plan.edn) or Round 2 min ≥ 8.5 (ladder target per Phase 1 fix-pass precedent). If Round 2 lands ≥ 9.0 we freeze without a Round 3.

## 13. Open questions / future work

**Known-deferred to v0.2:**
- How does `read/splice/rewrite/compose/fork/promote` interact with content-addressing when subtree identity changes mid-edit?
- Does alias lowering extend to v0.2 edit operations or stay parse-time-only?

**Known-deferred to v0.3:**
- Skill record storage layout in `persistence.fact` datoms
- Skill lookup semantics when `:call-skill :skill/foo@v3` references a version not in storage

**Known-deferred to v0.4:**
- Pareto Vector Metric schema finalization
- Stats emission schema for `{uses, success, cost-usd, latency-ms}` per `:id`

**Known-deferred to Phase 3:**
- Mutation operator catalog + LLM-as-mutation prompt design
- MCTS node selection + rollout harness
- MIPROv2 integration point at `:llm-call` leaves
- Evolutionary outer loop + AST crossover semantics

**Known-deferred indefinitely:**
- Fine-tune regime (waits for ≥ 1000 uses on any skill)
- Cross-project skill sharing (multi-tenant)

---

## Appendix A — Meta-note for ARIS reviewers

This module is deliberately small. If you feel the urge to flag "what about the edit API / skill library / optimizer?" — that urge is correct, and all of it is named in §3 with explicit v0.x upgrade paths. v0.1 is a content-addressing foundation. Every Phase 1 module also shipped minimal first; the pattern is load-bearing.

The one architectural choice worth genuine R3 scrutiny is the alias-lowering decision in §6.2. Is lowering at parse-time (rather than extending the spec) the right call? The alternative is spec extension, which would require a spec minor version bump and reopens the Phase 1 spec freeze. Lowering keeps the spec stable; the cost is lossy round-trip for aliased inputs. Argue it either way.
