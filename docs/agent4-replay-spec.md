# Agent 4 — Counterfactual Replay + Continual Learning Architectural Spec

*Research spec produced by a deep-research agent on 2026-04-20. Archived verbatim as input to Module 4 (`persistence.replay`).*

---

## 0. Research synthesis

- **CAMO (Counterfactual Analysis via Matched Outcomes)** — *aligned randomness*: factual and counterfactual rollouts with **identical seeds**, identical simulator state, identical environmental draws. Paired differences have `Var(X-Y) = Var(X) + Var(Y) - 2Cov(X,Y)`; aligned seeds drive `Cov(X,Y)` near 1, so ~10-100× fewer samples for significant effect. Simulator-internal counterfactuals — the branch lives inside the executor.

- **AgentHER** — extends Hindsight Experience Replay (Andrychowicz 2017) to LLM agents. When trajectory fails original goal, relabel final state *as if it were the goal*; trajectory becomes positive example for different goal. Requires trajectory schema where goal is a separable field.

- **AgenTracer** — counterfactual fault injection: given multi-agent failure, intervene at each step ("what if agent B had said X?") and measure which single intervention rescues the trajectory. Identifies pivotal decision — the one step that matters most.

- **Abduct-Act-Predict (AAP)** — Pearl's three-step ladder operationalized for LLMs: (1) **abduct** latent state from observed trajectory, (2) **act** — apply intervention (do-operator), (3) **predict** — roll forward with abducted latents fixed but intervention applied.

- **Experiential Reflective Learning (ExpeL)** — trajectories → natural-language heuristics via cross-trajectory comparison. When factual and counterfactual diverge, LLM asked "what rule explains why counterfactual succeeded?" — rule enters skill library.

- **DPO from trajectories** — Agent-DPO, TR-DPO (2025-26): don't need pairwise human preferences; **factual-vs-counterfactual trajectories with outcome labels** give preference signal for free. Prefix-aligned pairs (same state, divergent action, divergent outcome) = exactly the `chosen / rejected` format.

- **Nate Herk pattern** — git-as-memory, weekly self-review. Each trading decision is a commit; branches are alternate strategies; `git diff` between outcomes is learning signal. Maps directly: a branch IS a counterfactual.

Core implication: **runtime must treat trajectories as content-addressed, branchable, seed-aligned immutable facts.** Same substrate as git, Datomic, or bitemporal Clojure memory. Counterfactual engine is thin layer on top.

## 1. Trajectory Schema (EDN)

```clojure
{:trajectory/id            #uuid "..."
 :trajectory/parent-id     #uuid "..."     ; nil for factual root; set for counterfactuals
 :trajectory/branch-point  42              ; step index where divergence began
 :trajectory/intervention  {:step 42 :field :action :new-value {...}}
 :trajectory/agent         "adaptive-trader-v2"
 :trajectory/goal          {:type :profit :target 50.0}      ; separable for HER
 :trajectory/seeds         {:llm 8471293 :tool 993122 :env 552811}
 :trajectory/started-at    #inst "2026-04-20T09:00:00Z"
 :trajectory/wall-clock-basis :recorded    ; or :now for live replay
 :trajectory/status        :completed      ; :running :failed :counterfactual
 :trajectory/outcome       {:pnl -4.2 :duration-s 890 :success? false}
 :trajectory/facts
 [{:step 0
   :t    #inst "2026-04-20T09:00:00Z"
   :state   {:balance 400.0 :position nil :regime :chop}
   :obs     {:btc-price 67420 :funding 0.01 :oi 12.3e9}
   :llm-in  {:prompt-hash "sha256:ab..." :context-ids [...]}
   :llm-out {:text "wait" :tokens 847 :logprobs [...] :model "opus-4.7"}
   :action  {:type :hold}
   :tool-calls []
   :random-draws  {:exploration 0.23 :sample-idx 4}}
  {:step 1 ...}]
 :trajectory/hash          "sha256:..."
 :trajectory/tags          #{:losing :adaptive-trader :regime-chop}}
```

Key design choices:
- **Parent-ID + branch-point + intervention** = lineage DAG. Counterfactuals are first-class trajectories.
- **Facts are step-indexed, not time-indexed.** Time is a field, enabling replay with `:wall-clock-basis :recorded`.
- **Seeds recorded per-domain** (LLM, tool, env) so any subsystem can be reseeded independently.
- **llm-out includes logprobs + prompt-hash.** Logprobs for DPO; prompt-hash detects schema drift between replay and record.
- **Goal is separable** — AgentHER requirement.

## 2. Determinism Requirements

| Source of non-determinism | Capture at record-time | Replay strategy |
|---|---|---|
| LLM sampling | `seed`, `temperature`, `model`, `prompt-hash`, full `llm-out` | Replay from cache by prompt-hash |
| Tool calls (pure) | input, output, tool-version | Replay from cache keyed by (tool, input-hash) |
| Tool calls (external API: Binance, Stripe, Supabase) | input, response, response-timestamp | **Replay from cache only.** Never re-call. |
| Time (`now`) | wall-clock at each step | Replay with `recorded` basis — inject clock as capability |
| Random draws (exploration, sampling) | every `(rand)` call | Replay from captured stream or reseed |
| File/DB reads | read values, not file contents | Replay from cache (bitemporal DB gives this for free) |
| User input | full text, timestamp | Replay from recorded value |

**What cannot be made deterministic:**
- Live market data past trajectory window — extrapolation, not replay. Mark counterfactuals as `:simulated` not `:replayed`.
- Third-party API side effects — cannot un-charge Stripe. Use `:mode :dry` for replay of side-effecting actions.
- Floating-point across hardware — bind trajectory to runtime signature if needed.

**Cooperation with effect handlers:** effect handlers are the capture mechanism. Every effectful call writes a fact through a handler. Replay installs a *replay handler* that reads cached response instead of executing.

## 3. Replay Engine API

```clojure
(defprotocol ReplayEngine
  (replay [this trajectory-id interventions opts])
  (branch [this trajectory-id at-step intervention])
  (compare [this traj-a-id traj-b-id]))

(defn replay
  [traj-id
   {:keys [interventions   ; vector of {:step n :field f :new-value v}
           mode            ; :deterministic | :resample | :live
           clock           ; :recorded | :now | (fn [step])
           effect-mode]}]  ; :cache-only | :simulated | :real-dry
  (let [traj (load-trajectory traj-id)
        new-traj (mk-trajectory :parent-id traj-id
                                :branch-point (min-step interventions)
                                :seeds (:trajectory/seeds traj))]
    (reduce
      (fn [acc step-idx]
        (let [original-fact (nth (:facts traj) step-idx)
              intervention (find-intervention interventions step-idx)
              fact (cond
                     (< step-idx (branch-point interventions))
                     original-fact
                     intervention
                     (apply-intervention original-fact intervention)
                     :else
                     (execute-step (last-fact acc)
                                   {:seed (:llm-seed (:seeds traj))
                                    :effect-handler (replay-handler traj mode)
                                    :clock clock}))]
          (conj-fact acc fact)))
      new-traj
      (range (count (:facts traj))))))
```

Four intervention points:
1. `:field :action` — replace agent's chosen action.
2. `:field :obs` — replace observation (what-if market had moved differently).
3. `:field :llm-out` — replace LLM output (what if had decided differently).
4. `:field :state` — replace internal state (what if balance had been higher).

## 4. Counterfactual Comparison

Given paired trajectories `A` (factual) and `B` (counterfactual):

- **Outcome delta:** `(- (:pnl outcome-B) (:pnl outcome-A))`
- **Decision divergence point:** first step where `action-A ≠ action-B`.
- **Causal attribution (AgenTracer-style):** for each step, compute *individual treatment effect* — outcome if intervening *only* there. Step with largest ITE = pivotal decision.
- **Cost delta:** tokens, wall time, USD.
- **Policy distance:** KL divergence between `llm-out` logprob distributions at shared prefix points.
- **Abduction consistency (AAP):** hidden-state guesses remain plausible under intervention?

## 5. Learning Loop

**DPO pair extraction:**
```clojure
(defn extract-dpo-pair [factual counterfactual]
  (when (and (= (prefix-to factual (:branch-point counterfactual))
                (prefix-to counterfactual (:branch-point counterfactual)))
             (> (outcome-delta factual counterfactual) threshold))
    {:prompt (llm-in-at factual (:branch-point counterfactual))
     :chosen (llm-out-at (better-of factual counterfactual) branch)
     :rejected (llm-out-at (worse-of factual counterfactual) branch)
     :margin (outcome-delta factual counterfactual)}))
```

**Skill promotion:** if counterfactual pattern ("wait 10 min after regime flip") beats factual across ≥20 paired replays with positive mean delta and p<0.05, emit skill:

```clojure
{:skill/trigger "regime just flipped to chop"
 :skill/action "wait 10 minutes before entering"
 :skill/evidence-trajectories [#uuid ... #uuid ...]
 :skill/expected-edge 2.3}
```

**Regression tests:** every trajectory with `:status :completed :success? true` becomes a golden test. Replay must produce same outcome.

**ExpeL reflection:** weekly, for top-10 counterfactual-vs-factual deltas, ask LLM: *"What rule explains the difference?"* Candidate rule enters review queue.

## 6. Concrete Project Mappings

### Adaptive Trader v2 (primary)

```clojure
;; Post-trade analysis: replay losing trade with wait-N sweep
(for [wait-min [0 5 10 15 30 60]]
  (replay losing-trade-id
          {:interventions [{:step entry-step
                            :field :action
                            :new-value {:type :wait :minutes wait-min}}]
           :effect-mode :cache-only}))
```
→ 6 trajectories, compare outcomes, find optimal wait. If wait=10 beats factual by >$2 on 20+ losing trades, promote skill.

Also: **ensemble counterfactual** — replay with different LLM temperatures/models at decision point, pick best. Feed as DPO data.

### GuestFlow Simo Feedback v2

Each Simo test session is a trajectory. Regression: factual's outcome = expected. Replay every trajectory on every deploy. 5 regressions become 5 regression tests generated from bug reports:
```clojure
(gen-regression-test
  {:trajectory simo-session-id
   :assertion (fn [traj] (= 1 (count-ack-messages traj)))})
```

### BankabilityAI regulator challenge

```clojure
(replay bankability-assessment-id
        {:interventions [{:step :wacc-input :field :state :new-value 0.10}]})
```
→ complete alternate assessment, auditable, one query.

### ModelForge tornado

N parallel counterfactuals, one per parameter:
```clojure
(pmap #(replay egh-model-id {:interventions [{:field :state :path [:params %] :new-value (perturb %)}]}) 
      tornado-params)
```
→ 24 counterfactuals, sort by `|outcome-delta|`, render tornado chart.

## 7. Prototype (Python, 115 lines)

```python
import hashlib, json, random, uuid, copy
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

@dataclass
class Fact:
    step: int
    state: dict
    obs: dict
    llm_in: dict
    llm_out: dict
    action: dict
    tool_calls: list = field(default_factory=list)
    random_draws: dict = field(default_factory=dict)

@dataclass
class Trajectory:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    branch_point: Optional[int] = None
    intervention: Optional[dict] = None
    goal: dict = field(default_factory=dict)
    seeds: dict = field(default_factory=lambda: {"llm": 0, "tool": 0, "env": 0})
    facts: list = field(default_factory=list)
    outcome: dict = field(default_factory=dict)

class EffectHandler:
    """Captures on record, replays from cache."""
    def __init__(self, mode="record", cache=None):
        self.mode, self.cache, self.calls = mode, cache or {}, []
    def call(self, kind, key, fn):
        if self.mode == "replay" and key in self.cache:
            return self.cache[key]
        result = fn()
        self.calls.append({"kind": kind, "key": key, "result": result})
        return result

def agent_step(state, obs, handler, rng) -> Fact:
    """Toy agent: decide to buy/wait based on LLM (mocked)."""
    prompt = f"price={obs['price']} regime={obs['regime']}"
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    llm_out = handler.call("llm", prompt_hash, lambda: {
        "text": "buy" if obs["regime"] == "trend" and rng.random() > 0.3 else "wait",
        "logprobs": [-0.2, -1.8]
    })
    action = {"type": llm_out["text"]}
    return Fact(step=state["step"], state=copy.deepcopy(state),
                obs=obs, llm_in={"prompt_hash": prompt_hash},
                llm_out=llm_out, action=action,
                random_draws={"expl": rng.random()})

def apply_action(state, obs, action):
    new = copy.deepcopy(state); new["step"] += 1
    if action["type"] == "buy":
        new["position"] = obs["price"]; new["balance"] -= obs["price"]
    elif action["type"] == "sell" and new.get("position"):
        new["balance"] += obs["price"]; new["pnl"] = obs["price"] - new["position"]
        new["position"] = None
    return new

def record(obs_stream, seeds) -> Trajectory:
    traj = Trajectory(seeds=seeds)
    handler = EffectHandler(mode="record")
    rng = random.Random(seeds["llm"])
    state = {"step": 0, "balance": 400.0, "position": None, "pnl": 0.0}
    for obs in obs_stream:
        fact = agent_step(state, obs, handler, rng)
        traj.facts.append(fact)
        state = apply_action(state, obs, fact.action)
    traj.outcome = {"pnl": state["pnl"], "balance": state["balance"]}
    traj._cache = {c["key"]: c["result"] for c in handler.calls}
    return traj

def replay(traj: Trajectory, interventions: list) -> Trajectory:
    """Aligned-randomness counterfactual (CAMO-style)."""
    new = Trajectory(parent_id=traj.id, seeds=traj.seeds,
                     branch_point=min(i["step"] for i in interventions),
                     intervention=interventions[0])
    handler = EffectHandler(mode="replay", cache=traj._cache)
    rng = random.Random(traj.seeds["llm"])  # ALIGNED SEED
    state = {"step": 0, "balance": 400.0, "position": None, "pnl": 0.0}
    for orig in traj.facts:
        intervention = next((i for i in interventions if i["step"] == orig.step), None)
        if orig.step < new.branch_point:
            fact = orig  # verbatim prefix
            _ = rng.random()  # keep rng aligned
        elif intervention:
            fact = copy.deepcopy(orig)
            setattr(fact, intervention["field"], intervention["new_value"])
        else:
            fact = agent_step(state, orig.obs, handler, rng)
        new.facts.append(fact)
        state = apply_action(state, orig.obs, fact.action)
    new.outcome = {"pnl": state["pnl"], "balance": state["balance"]}
    return new

def compare(a: Trajectory, b: Trajectory):
    divergence = next((i for i, (fa, fb) in enumerate(zip(a.facts, b.facts))
                       if fa.action != fb.action), None)
    return {"pnl_delta": b.outcome["pnl"] - a.outcome["pnl"],
            "divergence_step": divergence,
            "factual_pnl": a.outcome["pnl"],
            "counterfactual_pnl": b.outcome["pnl"]}

if __name__ == "__main__":
    obs_stream = [{"price": 100, "regime": "chop"},
                  {"price": 95,  "regime": "trend"},
                  {"price": 110, "regime": "trend"},
                  {"price": 108, "regime": "chop"}]
    factual = record(obs_stream, seeds={"llm": 42, "tool": 0, "env": 0})
    print("Factual:", factual.outcome)
    cf = replay(factual, [{"step": 1, "field": "action", "new_value": {"type": "wait"}}])
    print("Counterfactual:", cf.outcome)
    print("Comparison:", compare(factual, cf))
```

## 8. Honest Tradeoffs

- **External API non-determinism is unsolvable without mocking.** For Binance/Stripe/Supabase: (a) record response into cache and replay from it, or (b) counterfactuals over side-effecting actions are *simulated*, not *replayed*. Explicit: `:mode :replayed` vs `:mode :simulated`.
- **Mock-vs-real tension.** Counterfactual replayed against cached market data past branch point extrapolates — real market would have reacted to hypothetical action (reflexivity). Mark counterfactuals extending past observation window as `:extrapolated` and discount their training weight.
- **Storage cost.** Full trajectory with logprobs ~5-50KB per step. 1000 trades × 50 steps × 20KB = 1GB. Mitigation: content-address facts, store logprobs only for decision steps, prune non-learning trajectories after 30d.
- **When NOT to replay:**
  - Side-effectful production actions (payments, emails sent) — replay is pointless or harmful.
  - Prompt schema drifted since record (prompt-hash mismatch) — replay meaningless.
  - High-reflexivity domains past observation window — extrapolation outweighs signal.
- **Sampling strategy.** Branch (a) losing trajectories (highest signal), (b) top-uncertainty decisions (high entropy LLM output), (c) user-flagged trajectories. Budget: ~5-10% of trajectories.
- **Abduction step is hard.** AAP assumes you can recover latent state. Approximation: treat cached LLM prompt + context as "latent," intervene on observable inputs. Weaker than true Pearl counterfactuals but operationally sufficient.

## Wire-in plan for Adaptive Trader v2 (next week)

1. Wrap every trade decision in effect handler (2 days).
2. Persist trajectories to Datomic/SQLite keyed by trade-id (1 day).
3. Cron nightly: for each losing trade, run `replay` with wait-sweep `[0,5,10,15,30,60]` min (1 day).
4. Aggregate weekly: if any wait value beats factual with p<0.05 across 20+ trades, emit skill + DPO pairs (2 days).
5. Ship DPO dataset to fine-tune pipeline; skill goes into playbook `crypto-agent-v2` reads at decision time.

Total: ~1 week to first counterfactual-driven improvement signal. Same layer serves GuestFlow regressions, BankabilityAI audits, ModelForge tornado, and vault replay without modification — only `agent_step` function changes per domain.
