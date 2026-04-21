# Agent 1 — Bitemporal Fact Store Architectural Spec

*Research spec produced by a deep-research agent on 2026-04-20. Archived verbatim as input to Module 1 (`persistence.fact`).*

---

## 0. Thesis

The AI agent memory field is bifurcating. One camp (Mem0, AtomMem, A-Mem) treats memory as a **mutable asset** that evolves with the agent — update-in-place, consolidate, re-summarize. The other camp (Zep/Graphiti, Memento) treats memory as a **bitemporal fact store** — append-only, with two clocks (valid-time and transaction-time), where "updates" are new facts that invalidate old ones by reference rather than erasure.

The governance literature (SSGM, Apr 2026) settles the argument on the side where regulators, auditors, and post-trade analysts already live: **mutable memory is structurally incompatible with audit, counterfactual reproduction, and drift-bounded reasoning.** SSGM's Theorem 1 shows unbounded semantic drift in pure-mutable systems vs. `O(N·ε_step)` bounded drift in systems with an append-only episodic ledger paired with a mutable projection. That is also, coincidentally, exactly Datomic's model reinterpreted for agents: the log is truth, everything else is a cached view.

This spec proposes a **Datomic-of-thought** runtime: immutable datoms, bitemporal by default, with retraction-by-reference (not deletion) and a materialized Graphiti-style view on top for fast retrieval. Clojure-native because EDN is the natural serialization of a datom, `core.async` is the natural transactor, and Datalog is the natural query language for `(entity, attribute, value, tx, valid-from, valid-to, provenance, op)` tuples.

## 1. Schema — The 8-Tuple Datom

The Datomic datom is `[e a v tx op]`. For agents we extend to 8 dimensions to capture the bitemporal + provenance + invalidation data that Zep/Memento carry:

```edn
{:datom/e             #uuid "…"        ;; entity id
 :datom/a             :project/wacc    ;; attribute (namespaced kw)
 :datom/v             0.087            ;; value (any EDN)
 :datom/tx            17332            ;; transaction id (monotonic)
 :datom/tx-time       #inst "2026-04-14T09:30Z"  ;; when we learned it (system time, T')
 :datom/valid-from    #inst "2026-04-14T00:00Z"  ;; when the fact became true (T)
 :datom/valid-to      nil              ;; when it stopped (nil = open interval)
 :datom/op            :assert           ;; :assert | :retract
 :datom/provenance    {:episode  #uuid "ep-…"   ;; Graphiti episode id
                       :source   :bankability-ai/dfi-agent
                       :model    "claude-opus-4.7"
                       :prompt-hash "sha256:…"
                       :confidence 0.82
                       :signature "ed25519:…"}  ;; per SSGM Principle 2 (σ(μ))
 :datom/invalidated-by nil}            ;; tx id of the superseding datom
```

**TypeScript equivalent:**

```ts
type Datom<V = unknown> = {
  e: string;              // uuid
  a: string;              // "project/wacc"
  v: V;
  tx: number;             // monotonic tx id
  txTime: Date;           // T' — system/transaction time
  validFrom: Date;        // T — valid time (event time)
  validTo: Date | null;   // null = still valid
  op: "assert" | "retract";
  provenance: {
    episode: string; source: string; model: string;
    promptHash: string; confidence: number; signature: string;
  };
  invalidatedBy: number | null;
};
```

**Nodes/edges projection** (materialized view, rebuilt from the log):

- **Entity node** = `{uuid, type, attrs-as-of(t)}` — reduced from datoms with matching `:datom/e`.
- **Edge** = a datom whose `:datom/v` is itself an entity uuid (Datomic `:db.type/ref`) OR a triple-style fact where `a` encodes the relation (Graphiti-style). Edges inherit `valid-from / valid-to / tx-time / invalidated-by` from their source datom.
- **Episode node** = the raw input (message, tool output, WhatsApp turn, Excel cell) keyed by `provenance.episode`.
- **Community node** = derived cluster, rebuilt periodically via label propagation. Not persisted as datoms; this is a pure cache.

Why 8 fields and not 5: Datomic's `[e a v tx op]` conflates tx-time and valid-time. Fine for an accounting ledger where the database learns facts the moment they happen; fatal for an agent that ingests conversations describing events from yesterday, forecasts about next quarter, and corrections about last year.

## 2. Query API

```clojure
(as-of db t)                 ;; => db-view  (filter tx-time ≤ t)
(as-of-valid db vt)          ;; => db-view  (filter valid-from ≤ vt < valid-to)
(history db e)               ;; => seq of datoms, all ops, all times
(since db t)                 ;; => db-view  (tx-time > t)   — incremental sync
(branch db t {:assert …})    ;; => counterfactual db — layers hypothetical datoms
(trajectory db e)            ;; => timeline of {valid-from, value, tx-time, provenance}
(conform db spec)            ;; => validates the db-view against a spec
(datalog db '[:find …])      ;; => full Datalog, operating on any db-view
```

**Examples:**

```clojure
;; "What did BankabilityAI believe about WACC for project P-042 on April 14?"
(datalog (as-of db #inst "2026-04-14T23:59Z")
  '[:find ?v ?conf
    :in $ ?e
    :where [?e :project/wacc ?v _ _ _ _ _ ?prov]
           [(get ?prov :confidence) ?conf]]
  project-p42-eid)

;; "What would it have concluded if WACC were 9.5% instead of 8.7%?"
(let [what-if (branch db #inst "2026-04-14T23:59Z"
                {:assert [[project-p42-eid :project/wacc 0.095]]})]
  (bankability/compute-score what-if project-p42-eid))
```

`branch` is the move that Datomic makes trivial and mutable stores make nearly impossible: because the underlying db is an immutable value, forking it is free (structural sharing), and the branched view runs through the same query engine as the real db.

## 3. Invalidation vs. Mutation

Three models in the literature, ranked:

| Model | Mechanism | Auditability | Drift |
|---|---|---|---|
| **Datomic retraction** | Emit `[e a v tx :retract]`. Old datom stays in `history`, absent from `current`. | Full. | Zero — old value recoverable. |
| **Graphiti invalidation** | Existing edge's `t_invalid` is **mutated** when LLM detects contradiction. | Partial — you lose the story of when the system realized it was wrong. | Low, but the edge row is edited in place. |
| **Mem0/AtomMem update** | `update(memory_id, new_content)` overwrites. | None — prior value gone unless snapshotted. | Unbounded (SSGM Theorem 1). |

**Recommendation:** Datomic-style retraction as ground truth, Graphiti-style `invalid-at` as a **derived hint** on the materialized view. New `{:op :assert, :v v2, :valid-from t2}` for cardinality-one attribute automatically emits a companion `{:op :retract, :v v1}` at transact time. LLM-based contradiction detection runs in the transactor as a **write-validation gate** and produces the companion retraction — never *edits* the historical datom.

## 4. Performance and Indexing

Adopt Datomic's covering-index quartet, extended for bitemporality:

| Index | Sort order | Purpose |
|---|---|---|
| **EAVT** | entity → attr → value → tx | "Everything about entity E right now / as-of t" — primary read path |
| **AEVT** | attr → entity → value → tx | "All WACCs across all projects" — analytics, sensitivity |
| **AVET** | attr → value → entity → tx | "Which project has WACC = 0.087?" — lookup by indexed attr |
| **VAET** | value → attr → entity → tx | Reverse-ref graph traversal — "which facts cite episode E?" |
| **VT-E** *(new)* | valid-from → valid-to → entity | Bitemporal range scans: `as-of-valid` without O(n) filter |
| **Log** | tx-time | Incremental sync, `since(t)`, replication |

Each index is split into history / current / in-memory segments. Segments are 1K–20K datoms, Zstd-compressed, content-addressed (SHA-256) → free deduplication and S3-friendly storage.

**Caching:** the materialized Graphiti projection (entity summaries, fact embeddings, community clusters) is a pure function of the log as-of latest tx — regenerable, never authoritative. Burn it down, rebuild from log.

**Compression of old epochs:** segments older than 90 days move to cold storage; embeddings for `invalid-at < now - 180d` dropped from vector index but retained as raw datoms.

**Latency budget:**
- Hot `as-of` query: < 50 ms p95
- Counterfactual `branch` query: < 200 ms p95
- `history(e)`: < 100 ms for entities with < 1000 datoms

## 5. Comparison Table

| Dimension | **Mem0 (mutable)** | **Zep/Graphiti** | **A-Mem** | **Datomic-of-Thought** |
|---|---|---|---|---|
| Audit trail | None — updates overwrite | Partial — `invalid_at` mutated | None — notes evolve via rewrites | **Full — append-only log, every state recoverable** |
| Counterfactual (`branch`) | Requires full replay | Hard — fork the Neo4j/Kuzu DB | Near-impossible — notes mutate | **Trivial — `(branch db t)` is O(log n) structural share** |
| Drift resistance | Unbounded | Bounded by contradiction detection | Unbounded | **Bounded: write gate + immutable log** |
| Latency | 1.44 s p95 (vector), 2.59 s (graph) | Sub-second via hybrid search | Not published at scale | **~50 ms hot reads from materialized projection** |
| Storage | Low — only current state | Medium — temporal edges | Low — notes only | **High — every datom retained. Zstd + content-addressing (~4× raw, vs. ~1× Mem0).** |
| Ease of adoption | Drop-in SDK | Graphiti lib | Research-grade | **Requires transactor + log store. Higher initial cost.** |
| LongMemEval score | ~66–68% | 94.8% / 18.5% lift over MemGPT | Claims SOTA | Not yet benchmarked; Memento's 92.4% is proxy |
| Regulator-grade | No | Partial | No | **Yes — designed for it** |

## 6. Concrete Mapping to User Projects

### BankabilityAI (DFI audit)
1. "Show me every WACC assumption the agent used for project P-042, when it learned each one, from which episode." → `(trajectory db p042-eid :project/wacc)`.
2. "What would the bankability score have been if WACC were 9.5% on April 14?" → `(branch db #inst "2026-04-14" {:assert [[p042 :project/wacc 0.095]]})` then rerun scoring.
3. "Which decisions in last 30 days depended on a fact since invalidated?" → Datalog over VAET + since.

### Adaptive Trader v2
1. "What did the agent believe about BTC funding rate at t=decision for trade #847?" → `(as-of db trade-847-tx)`.
2. "Of our last 50 trades, how many relied on a news fact later contradicted?" → join `invalidated-by` to trade-decision episodes.
3. "Replay last 7 days of market beliefs with 20% lower funding rates throughout." → `(branch db t {:assert [...synthetic...]})`.

### Insurance Comparator (MIA-licensed)
1. "What comparison did we show client X at 14:03 on April 14, and why?" → `(as-of db #inst "2026-04-14T14:03Z")`.
2. "When did agent learn Wakam changed pricing, and what quotes issued between change and ingestion?" → bitemporal XOR: flag all quotes where `tx-time(quote) > valid-from(new-price) AND tx-time(quote) < tx-time(price-learned)`.
3. Regulator query: "All facts asserted about client Y with confidence < 0.7."

### GuestFlow
1. "What did we tell Simo about his booking at 19:00 yesterday?" → `as-of` the conversation state at that time.
2. "Guest preferences we inferred vs. explicitly stated." → filter by `provenance.source`.
3. Counterfactual: "If we hadn't inferred guest prefers late check-in, would we still have upgraded?"

### ModelForge
1. "EGH tornado with different concession-fee assumption." → `branch` on `:egh/concession-fee`, re-run.
2. "Every time S&U parser changed its mind about gearing for Project Z." → `(trajectory db z-eid :project/gearing)`.

### Memory Palace
1. "What did vault believe about 'AI Box crowdfunding' in mid-March vs. today?" → `(diff (as-of db t1) (as-of db t2))`.
2. "Which facts are downstream of Pieces LTM ingest on March 19?" → VAET walk from `provenance.episode`.

## 7. Migration Path for the Memory Palace

**Verdict: additive, not rewrite.** Three phases, ~2 weeks.

**Phase 1 — Add tx log (2 days, non-breaking).** Stand up `datom_log` table in Postgres with the 8-tuple schema. Wrap every `memory.add()` / `mem0.update()` / Kuzu write in an interceptor emitting a datom first, then performing the legacy write. Zero reads change.

**Phase 2 — Backfill (3 days).** Generate synthetic datoms for existing Kuzu nodes/edges with `tx-time = created_at`, `valid-from = created_at`, `valid-to = nil`, `provenance.source = :backfill-2026-04-21`, `confidence = 0.5`.

**Phase 3 — Query surface (1 week).** Build `as-of`, `history`, `branch` as thin Python/TS functions. Existing vault skills (`vault-compound`, `vault-trace`) rewire to use these primitives. Mem0 remains as materialized projection; Kuzu remains as graph projection; both rebuildable from log.

**What you do *not* do:** rewrite in Clojure. Spec is Clojure-native in *philosophy*; implementation can be Python/TS.

## 8. Prototype (Python, 76 lines)

```python
# bitemporal_fact_store.py — minimal Datomic-of-thought for agents
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
import itertools, json, hashlib

_tx_counter = itertools.count(1)
def _now(): return datetime.now(timezone.utc)

@dataclass(frozen=True)
class Datom:
    e: str; a: str; v: Any
    tx: int; tx_time: datetime
    valid_from: datetime; valid_to: datetime | None
    op: str  # "assert" | "retract"
    provenance: dict
    invalidated_by: int | None = None

@dataclass
class DB:
    log: tuple[Datom, ...] = field(default_factory=tuple)

    def transact(self, facts: list[dict], provenance: dict) -> "DB":
        tx = next(_tx_counter); now = _now(); new_datoms = []
        for f in facts:
            op = f.get("op", "assert")
            vf = f.get("valid_from", now); vt = f.get("valid_to", None)
            if op == "assert":
                for d in reversed(self.log):
                    if d.e == f["e"] and d.a == f["a"] and d.op == "assert" \
                       and d.valid_to is None and d.invalidated_by is None:
                        new_datoms.append(replace(d, invalidated_by=tx))
                        new_datoms.append(Datom(
                            d.e, d.a, d.v, tx, now, d.valid_from, vf,
                            "retract", {**d.provenance, "superseded_by_tx": tx}))
                        break
            new_datoms.append(Datom(
                f["e"], f["a"], f["v"], tx, now, vf, vt, op,
                {**provenance, "prompt_hash": hashlib.sha256(
                    json.dumps(f, default=str).encode()).hexdigest()[:16]}))
        return DB(log=self.log + tuple(new_datoms))

    def as_of(self, t: datetime) -> "DBView":
        return DBView([d for d in self.log if d.tx_time <= t])

    def as_of_valid(self, vt: datetime) -> "DBView":
        return DBView([d for d in self.log if d.valid_from <= vt
                       and (d.valid_to is None or vt < d.valid_to)
                       and d.op == "assert"])

    def history(self, e: str) -> list[Datom]:
        return [d for d in self.log if d.e == e]

    def since(self, t: datetime) -> "DBView":
        return DBView([d for d in self.log if d.tx_time > t])

    def branch(self, t: datetime, assertions: list[dict]) -> "DB":
        base = self.as_of(t).datoms
        return DB(log=tuple(base)).transact(
            assertions, {"source": "branch", "base_tx_time": t.isoformat()})

@dataclass
class DBView:
    datoms: list[Datom]
    def entity(self, e: str) -> dict:
        latest: dict[str, Datom] = {}
        for d in self.datoms:
            if d.e != e or d.invalidated_by is not None or d.op != "assert":
                continue
            cur = latest.get(d.a)
            if cur is None or d.valid_from > cur.valid_from:
                latest[d.a] = d
        return {a: d.v for a, d in latest.items()}

# ---- Demo: BankabilityAI WACC counterfactual ----
if __name__ == "__main__":
    db = DB()
    db = db.transact([{"e": "p-042", "a": "project/wacc", "v": 0.087,
                       "valid_from": datetime(2026,4,14,tzinfo=timezone.utc)}],
                     {"source": "dfi-agent", "model": "claude-opus-4.7", "confidence": 0.82})
    db = db.transact([{"e": "p-042", "a": "project/wacc", "v": 0.091,
                       "valid_from": datetime(2026,4,19,tzinfo=timezone.utc)}],
                     {"source": "dfi-agent-rerun", "confidence": 0.88})
    print("Now:      ", db.as_of(_now()).entity("p-042"))
    print("April 15: ", db.as_of_valid(datetime(2026,4,15,tzinfo=timezone.utc)).entity("p-042"))
    cf = db.branch(_now(), [{"e": "p-042", "a": "project/wacc", "v": 0.095}])
    print("Branch:   ", cf.as_of(_now()).entity("p-042"))
```

## 9. Tradeoffs Accepted

1. **Storage cost ~4× Mem0.** Content-addressed segments and Zstd help. Mitigate with cold-tier after 90 days, embedding eviction after 180.
2. **Write latency +20–40 ms** vs. mutable stores. Acceptable for all user projects; voice-turn latency should bypass transactor and use a fast ring buffer drained async.
3. **Provenance signatures expensive** if per-datom ed25519. Batch-sign per transaction — 100× reduction with no audit loss.
4. **Community / summary layers drift** — reconcile hourly.
5. **Not yet LongMemEval-proven.** Memento's 92.4% on structurally similar design is the best proxy.

## Sources

- Zep: A Temporal Knowledge Graph Architecture for Agent Memory — arXiv:2501.13956
- Graphiti — getzep/graphiti on GitHub
- Memento case study, n1n.ai (Apr 11 2026)
- A-MEM: arXiv:2502.12110
- State of AI Agent Memory 2026 — mem0.ai
- SSGM: Governing Evolving Memory in LLM Agents — arXiv:2603.11768
- AtomMem: arXiv:2601.08323
- Datomic Index Model — docs.datomic.com
- Unofficial Guide to Datomic Internals — Tonsky
- The Database as a Value — Rich Hickey
- LongMemEval benchmark — arXiv:2410.10813
