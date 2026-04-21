# Agent 2 — Homoiconic Plan DSL Architectural Spec

*Research spec produced by a deep-research agent on 2026-04-20. Archived verbatim as input to Module 3 (`persistence.plan`).*

---

## 0. Thesis

The sweet spot between **SmolAgents' "code IS the action language"** and **DSPy's "programs IS a declarative DAG"** is a two-layer homoiconic plan: **EDN/AST nodes for the control skeleton** (sequence, parallel, choice, loop, tool-call, reflect, checkpoint) with **prose and code as leaves** (`:llm-call` body, `:tool-call/code` body). The skeleton is optimizable (search, rewrite, splice); the leaves stay expressive.

## 1. Plan AST Schema (EDN)

```clojure
;; Every node: [:node-type {attrs} & children]
;; All nodes carry :id (content-addressed sha256), :meta {cost latency success-rate}.

;; --- Control structures ---
[:seq     {:id …}                 child1 child2 …]         ; sequential
[:par     {:id … :join :all|:any} child1 child2 …]         ; parallel fan-out
[:choice  {:id … :selector expr}  [:case pred branch] …]   ; conditional/switch
[:loop    {:id … :while pred :max-iter 10} body]           ; bounded loop
[:race    {:id … :timeout-ms 30000} child1 child2 …]       ; first-wins

;; --- Effect leaves ---
[:tool-call {:id … :tool :http/get :args {:url ?u}} ]
[:llm-call  {:id … :signature in->out :prompt "…" :model :opus-4.7
             :temperature 0.2 :constraints {:max-tokens 1500}}]
[:code      {:id … :lang :python :sandbox :e2b
             :body "result = search_web(…); …"}]           ; SmolAgents-style leaf

;; --- Cognitive operators ---
[:reflect    {:id … :criteria ["correctness" "cost"]} target-id]
[:checkpoint {:id … :persist :vault :tier :L1}]            ; content-addressed snapshot
[:branch     {:id … :strategy :beam :k 3} plan-variant]    ; speculative search node
[:verify     {:id … :prover :z3 :dsl pot-expr}]            ; Proof-of-Thought gate
[:call-skill {:id … :skill :skill/draft-boa-letter@v3 :args m}]

;; --- Binding / dataflow ---
[:let  {:id … :bindings {:quote q}} body]
[:ref  :symbol]                                             ; reference a bound value
```

Every node is a plain EDN vector. IDs are `(sha256 (pr-str node-without-meta))`, so plans are **content-addressed Merkle DAGs**. Two agents that independently derive the same plan fragment hash-collide and share storage.

## 2. Homoiconicity Contract

**Allowed operations (require `:plan/edit` capability):**
- `(plan/read plan path)` — zipper-style descent.
- `(plan/splice plan path subtree)` — replace subtree.
- `(plan/compose plan-a plan-b :at path)` — merge two plans.
- `(plan/rewrite plan matcher transform)` — core.match pattern rewrite.
- `(plan/fork plan)` — new content-addressed variant (parent pointer preserved).
- `(plan/promote plan path :skill/my-name)` — extract subtree as reusable skill.

**Forbidden without explicit user capability:**
- Editing `:verify` nodes (defeats Proof-of-Thought guarantees).
- Editing `:checkpoint` persistence tiers.
- Modifying ancestor nodes of the currently-executing frame.
- Granting itself new tool permissions (separate capability system).
- Introducing unbounded `:loop` (max-iter mandatory).

## 3. Skill Library Semantics

**Skill record:**
```clojure
{:skill/name      :skill/draft-boa-letter
 :skill/version   3
 :skill/parent    2
 :skill/signature {:in [:issue :severity] :out :letter}
 :skill/ast       [:seq …]
 :skill/embedding #float-vec[…]
 :skill/doc       "Drafts a BOA escalation letter…"
 :skill/stats     {:uses 47 :success 0.91 :avg-cost-usd 0.03}
 :skill/tests     [{:in … :expect-match …} …]}
```

**Promotion criterion** — Voyager promotes on first success (too eager). Memento-Skills on failure-driven rewrite (too reactive). Hybrid:

> Promote subtree `T` to a named skill when:
> 1. `T` has been executed ≥ 3 times across ≥ 2 distinct parent plans,
> 2. rolling success rate ≥ 0.8,
> 3. structural cosine similarity across occurrences ≥ 0.9,
> 4. an LLM-generated docstring for `T` round-trips through retrieval (query the skill library with the docstring; `T` must be top-1).

**Composition:** skills are first-class AST subtrees. `[:call-skill …]` inlines at compile time for small skills, late-binds for versioned/remote ones. Versioning immutable (`@v3`); deprecation is a flag, not deletion.

## 4. Optimization Loop

**The signal:** a vector metric `{:success ℝ :latency-ms ℝ :cost-usd ℝ :user-rating ℝ}` collapsed by Pareto dominance — not a scalar.

**Four search regimes:**

| Regime | When | Operator | Reference |
|---|---|---|---|
| **Bootstrapped instruction/demo search (MIPROv2)** | Plan topology fixed; only `:llm-call` prompt + few-shot demos vary | Bayesian Optimization over `(instruction × demos)` per `:llm-call` slot | DSPy MIPROv2 |
| **MCTS over plan tree** | Small discrete branching (tool A vs B; reflect vs retry) | UCT on children of `:choice`/`:branch`; rollout = full execution; value = Pareto score | KernelEvolve selective memory |
| **Evolutionary over AST** | Topology must change (add/remove steps, re-order, insert `:verify`) | LLM-as-mutation + AST crossover (subtree swap at compatible type points) | Genetic Improvement 2025-26; LLaMEA-SAGE |
| **Gradient-estimated (BootstrapFinetune / SOAR)** | Weights to fine-tune or many samples; plan stable | Trace-logging → synthetic dataset → fine-tune or REINFORCE | SOAR |

**Recommended ladder:** always run MIPROv2 first. Promote to MCTS when prompt tuning saturates. Promote to evolutionary when MCTS can't find new topologies within budget. Fine-tune last for skills with ≥ 1000 uses.

**Critical trick from KernelEvolve:** the mutation LLM gets **runtime diagnostics in its prompt** — not just "this plan failed" but "step `:llm-call#a3f` returned wrong JSON shape, `:tool-call#b2e` took 14s (p95 budget 3s), `:verify#c1d` rejected claim 'IRR > 13'". Each failed execution becomes a **structured gradient** into the next mutation.

## 5. Compiler

**Dual-target:**

1. **Interpreted EDN** (default, reflex path). ~500-line Clojure interpreter walks the AST with CPS evaluator. Every node emits an OpenTelemetry span keyed by node `:id`.
2. **Compiled Python** (SmolAgents-style, when subtree is purely `[:seq [:tool-call…] [:code…] [:tool-call…]]` with no `:reflect`/`:branch`). Compiler lowers to a single `CodeAgent` block because "search_web + parse_json + max(lambda)" in one Python block is fewer round-trips than three JSON tool-calls.

Compiler chooses per-subtree based on purity analysis.

## 6. Concrete Mappings

### Conductor tracks → plan ASTs

```clojure
[:seq {:id "track/crypto-v2"}
  [:call-skill {:skill :skill/aris-phase0-scope}]
  [:par {:join :all}
    [:call-skill {:skill :skill/worker-memory-layer}]
    [:call-skill {:skill :skill/worker-market-intel}]
    [:call-skill {:skill :skill/worker-playbook-engine}]]
  [:checkpoint {:tier :L1}]
  [:verify {:prover :aris :gate :round-4 :min-score 9.0}]
  [:reflect {:criteria ["scope-drift" "coverage"]}]]
```

ARIS rounds become a Pareto signal; MCTS picks which `:par` branch to spawn first based on past success rate.

### Adaptive Trader v2 playbook

Each trading decision = `[:choice {:selector market-regime} …]` where branches are named skills `:skill/trend-entry@v7`, `:skill/reversion-entry@v3`. Post-trade analysis rewrites playbook via `(plan/splice playbook [:choices 2 :body] new-skill)`. Pareto metric `{:pf :sharpe :max-dd :trade-count}` exposes regime-dependence.

### ModelForge sector templates

Each template IS a plan. S&U parser output is the selector:
```clojure
[:choice {:selector (fn [su] (classify-sector su))}
  [:case :energy-greenfield [:call-skill :skill/egh-template@v12]]
  [:case :transport-concession [:call-skill :skill/transport@v4]]]
```
Core (DSRA/WC/fees) = `[:par]` inserted at right path in every sector. Extensions = `[:choice]` branches.

### BankabilityAI ARIS gates

Each ARIS round = `[:verify {:prover :aris-9-3 :criteria [...]}]` blocking the parent `:seq`. The 4→7→7.8→9/10 trajectory is literally an MCTS rollout.

### GuestFlow per-hotel playbook

Top-level `[:call-skill :skill/hotel-base@v2]` composed with hotel-specific overrides via `(plan/compose base-plan hotel-overrides :at [:onboarding])`. Per-guest plans **forked** from hotel plan at conversation start, so Simo requests ("don't double-ack") become AST rewrites not code PRs.

### Insurance Comparator (10 verticals)

Each vertical = one plan; all 10 share common prefix (lead capture, KYC, Wakam/Seyna dispatch) via `[:call-skill]`. When auto gets a better "triage" sub-plan, it auto-promotes to shared if uses ≥ 3 verticals pass gate.

## 7. Relationship to DSPy

**Orthogonal but complementary.**

- DSPy optimizes **individual `:llm-call` leaves** beautifully (MIPROv2). We delegate that.
- DSPy has **no first-class notion of skeleton mutation** — `dspy.Module` topology fixed at `__init__`. Our AST is mutable by design.
- DSPy has **no skill library with promotion criteria** — `BootstrapFewshot` caches demos per-module, not reusable skill fragments.
- DSPy has **no homoiconicity contract** — programs are Python classes, not inspectable data.

**Integration strategy:** each `[:llm-call]` node carries optional `:dspy/module` attribute. At compile time, a DSPy `Predict`/`ChainOfThought`/`ReAct` module instantiated with that signature. MIPROv2 runs per-node as inner optimizer. Our evolutionary/MCTS layer is outer optimizer over plan skeleton.

## 8. Prototype (Clojure, 94 lines)

```clojure
(ns cognitive.runtime
  (:require [clojure.edn :as edn]
            [clojure.core.match :refer [match]]))

;; ---------- AST ----------
(defn node [type attrs & children]
  (let [body (into [type (merge {:id (hash [type attrs children])} attrs)]
                   children)]
    body))

(def plan-v0
  (node :seq {}
    (node :llm-call {:signature 'q->draft :prompt "Draft answer to {{q}}"
                     :model :haiku :temperature 0.3})
    (node :tool-call {:tool :web/search :args {:q :ref/q}})
    (node :llm-call {:signature 'draft+evidence->final :prompt "Revise {{draft}} with {{evidence}}"})
    (node :verify   {:prover :heuristic :min-length 80})
    (node :reflect  {:criteria ["correctness" "cost"]})))

;; ---------- Interpreter ----------
(defn eval-node [ctx [t attrs & children]]
  (case t
    :seq       (reduce (fn [c n] (assoc c :last (eval-node c n))) ctx children)
    :llm-call  (update ctx :trace conj {:id (:id attrs) :cost 0.002 :ok? true})
    :tool-call (update ctx :trace conj {:id (:id attrs) :cost 0.0   :ok? true})
    :verify    (update ctx :trace conj {:id (:id attrs) :ok? (rand-nth [true true false])})
    :reflect   ctx))

;; ---------- Homoiconic edits ----------
(defn rewrite [plan matcher transform]
  (if (matcher plan)
    (transform plan)
    (if (vector? plan)
      (into (subvec plan 0 2) (map #(rewrite % matcher transform) (drop 2 plan)))
      plan)))

;; ---------- Mutation operators ----------
(defn mut-insert-retry [plan]
  (rewrite plan
    #(and (vector? %) (= :llm-call (first %)))
    (fn [n] (node :loop {:while :not-ok :max-iter 2} n))))

(defn mut-swap-model [plan]
  (rewrite plan
    #(and (vector? %) (= :llm-call (first %)))
    (fn [[t a & c]] (into [t (assoc a :model :opus-4.7)] c))))

(def mutations [mut-insert-retry mut-swap-model])

;; ---------- Evaluator (Pareto) ----------
(defn evaluate [plan & {:keys [n] :or {n 5}}]
  (let [traces (repeatedly n #(:trace (eval-node {:trace []} plan)))
        flat   (apply concat traces)]
    {:success  (/ (count (filter :ok? flat)) (max 1 (count flat)))
     :cost-usd (reduce + (keep :cost flat))
     :ast-size (count (flatten plan))}))

(defn dominates? [a b]
  (and (>= (:success a)  (:success b))
       (<= (:cost-usd a) (:cost-usd b))
       (or (> (:success a)  (:success b))
           (< (:cost-usd a) (:cost-usd b)))))

;; ---------- Evolutionary outer loop ----------
(defn optimize [plan budget]
  (loop [frontier [[plan (evaluate plan)]] step 0]
    (if (>= step budget)
      (first (sort-by #(- (:success (second %))) frontier))
      (let [[p _]     (rand-nth frontier)
            m         (rand-nth mutations)
            p'        (m p)
            s'        (evaluate p')
            survivors (remove (fn [[_ s]] (dominates? s' s)) frontier)]
        (recur (conj survivors [p' s']) (inc step))))))
```

**What this shows:** (1) plan-as-EDN is one `pr-str` away from Git-storable; (2) `rewrite` is the minimum homoiconicity API; (3) mutations are ordinary functions, so an LLM can propose new ones; (4) Pareto frontier is ~6 lines and beats scalar-score optimization; (5) same EDN plan would lower to SmolAgents Python — dual-target free.

## 9. Four Tensions Resolved

| Tension | Resolution |
|---|---|
| **Code vs data** | Skeleton is data (optimizable); `:code` and `:llm-call` bodies are code/prose (expressive). Compile pure subtrees to SmolAgents Python. |
| **Prose CoT vs structured plan** | AST with prose leaves. `:llm-call/prompt` stays as templated prose; control stays as AST. |
| **When to promote skill** | 4-gate criterion: uses ≥ 3 × parents ≥ 2 × success ≥ 0.8 × retrieval round-trip. Stricter than Voyager, more principled than Memento-Skills. |
| **Search over plan structure** | Layered: MIPROv2 (leaves) → MCTS (choice nodes) → Evolutionary (topology) → Fine-tune (stable hot skills). KernelEvolve-style diagnostic-enriched mutation prompts. |

## Sources

- DSPy — dspy.ai; MIPROv2 3-stage BO optimizer
- KernelEvolve — Meta Engineering 2026
- Voyager — arXiv:2305.16291
- Proof of Thought — arXiv:2409.17270
- Memento-Skills — VentureBeat, arXiv:2603.18743
- Armin Ronacher — A Language for Agents (2026)
- SmolAgents — code > JSON as action language
- Open Agent Specification — arXiv:2510.04173
- Declarative DSL for LLM workflows — arXiv:2512.19769
- Genetic Improvement for LLM code — Ital-IA 2025
- LLaMEA-SAGE — arXiv:2601.21511
- SOAR — arXiv:2507.14172
- Agent Skills SoK — arXiv:2602.12430
