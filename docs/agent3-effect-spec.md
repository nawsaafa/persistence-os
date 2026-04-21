# Agent 3 — Effect Handler Safety Layer Architectural Spec

*Research spec produced by a deep-research agent on 2026-04-20. Archived verbatim as input to Module 2 (`persistence.effect`).*

---

## 1. Effect catalog

An *effect* is a labeled operation the agent **performs**. It suspends execution and hands a continuation `k` to whichever handler is in scope. Catalog intentionally small — more effects = more surface to audit.

```edn
;; edn-form interface
{:llm/call      {:args {:model kw? :messages [msg] :tools [tool] :temperature num? :max-tokens int?}
                 :returns {:text str? :tool-calls [tc] :usage usage :fingerprint str?}}
 :tool/call     {:args {:name kw? :input map? :tenant-id str?}                :returns {:result any? :error any?}}
 :mem/read      {:args {:tier kw? :query str? :scope map?}                    :returns {:hits [fact]}}
 :mem/write     {:args {:tier kw? :fact map? :valid-from inst :recorded-at inst} :returns {:id uuid}}
 :decide        {:args {:question str? :options [opt] :rationale str?}        :returns {:choice any? :confidence num?}}
 :ask-user      {:args {:prompt str? :options [str?] :timeout-ms int?}        :returns {:answer any?}}
 :emit-artifact {:args {:kind kw? :path str? :bytes bytes? :meta map?}        :returns {:uri str?}}
 :sleep         {:args {:ms int?}                                             :returns nil}
 :random        {:args {:kind kw? :params map?}                               :returns {:value any?}}
 :env/read      {:args {:key str?}                                            :returns {:value str? :source kw?}}
 :net/fetch     {:args {:url str? :method kw? :headers map? :body any?}       :returns {:status int? :body any?}}
 :secret/use    {:args {:name kw? :purpose str?}                              :returns {:handle opaque}}   ;; never returns raw
 :cost/charge   {:args {:units num? :currency kw? :category kw?}              :returns {:remaining num?}}
 :clock/now     {:args {}                                                     :returns {:ts inst}}
 :audit/emit    {:args {:kind kw? :payload map?}                              :returns nil}}
```

Why these additions: `env/read`+`secret/use` let the `ANTHROPIC_API_KEY` silent override failure show up in an audit. `clock/now` and `random` must be effects (not `System.currentTimeMillis`) or the replay engine cannot reproduce the run. `cost/charge` exists because "cost budget" belongs in the same plane as policy, not a sidecar.

## 2. Handler interface

Follows Pangolin directly: a handler is a set of clauses keyed by `:op`. Each clause receives `(args, k, ctx)`.

```clojure
(defprotocol Handler
  (handle [this op args k ctx]))

;; EDN descriptor
{:handler/id     :policy-bankability
 :handler/wraps  #{:llm/call :tool/call :decide :mem/write}
 :handler/state  {:rule-version "2026-04-20" :decisions 0}
 :handler/clauses {:decide (fn [args k ctx] ...)}}
```

Three features from Koka we keep: **named handlers**, **masked effects** (`(mask :audit (body))` lets a handler emit audit events without its own audit-wrap catching them), **tail-resumptive fast path** (handlers that always call `k` exactly once compile to direct function call — critical for rate-limit perf).

## 3. Handler stack example — BankabilityAI

```clojure
(defn bankability-stack [tenant-id track-id]
  (-> raw-runtime
      rate-limit-handler        ;; innermost: throttle vendor QPS
      retry-handler             ;; retries invisible to layers above
      cache-handler             ;; deterministic key; hit bypasses rate-limit+retry
      dry-run-handler           ;; if :mode :dry-run, short-circuit
      pii-redact-handler        ;; strips PII before outbound
      policy-handler            ;; allow | deny | require-approval
      cost-budget-handler       ;; reject if projected $ > budget
      audit-handler))           ;; outermost: logs BOTH attempted and resolved
```

**Why outer→inner this way:**

- `audit` outermost sees *intent*, not just survivors. Regulators want denied attempts too.
- `cost-budget` above `policy` because budget IS first-class policy, evaluated before semantic rules.
- `policy` above `dry-run` so *dry-run itself is policy-gated*.
- `cache` above `retry` so a cached result never triggers retries. Above `rate-limit` so cache hits don't burn quota.
- `rate-limit` innermost to see actual vendor traffic.

**When to break the order:**
- If `:secret/use` inside `policy` (HMAC), put `secret` below `audit` so handle is logged but raw key isn't.
- Kill-switch for Trader sits *above* `audit` — a killed agent should produce zero audit spam.
- PII redaction is *not* above policy: policy often needs PII to evaluate. Put it just above `cache`.

## 4. Policy as data (EDN)

Since runtime is EDN, policies are plain Clojure data interpreted by a small evaluator (~200 LOC).

```edn
{:policy/id      :bankability-v3
 :policy/version "2026-04-20"
 :policy/principal-attrs [:role :tenant-id :clearance]
 :rules
 [{:id :r1-regulator-audit-required
   :when [:and [:op= :decide] [:contains? [:args :tags] :regulator-facing]]
   :require [:handler-present? :audit-chain]
   :on-fail :deny}

  {:id :r2-assumption-change-logged
   :when [:and [:op= :mem/write] [:= [:args :tier] :modelforge-assumption]]
   :effect [:emit :audit {:kind :assumption-change :before [:prev] :after [:args :fact]}]
   :on-fail :deny}

  {:id :r3-no-prod-writes-in-dry-run
   :when [:and [:mode= :dry-run] [:op-in #{:tool/call :emit-artifact}]
          [:matches? [:args :name] #"^(stripe|supabase-prod|binance).*"]]
   :on-fail :deny-silently}

  {:id :r4-decision-needs-rationale
   :when [:op= :decide]
   :require [:non-empty? [:args :rationale]]
   :on-fail :require-approval}]}
```

Pure fn `(decide policy principal op args) -> {:verdict :allow|:deny|:require-approval :reasons [...]}`. Because data: (a) GuestFlow tenants get per-tenant policy diffs; (b) ModelForge ships policy *with* every artifact so regulator re-evaluates offline; (c) `git diff` on policy is change log.

## 5. Sharing handlers across agents

Three layers:
1. **Platform handlers** (all agents): `audit`, `retry`, `rate-limit`, `cache`, `clock`, `random`, `cost-budget`. In `cog.handlers.core`.
2. **Domain handlers** (shared by agents in same domain): `pii-redact`, `position-limit` (Trader), `mia-compliance` (Insurance).
3. **Agent stack** (per agent): composes 1+2 in the order the agent needs.

```clojure
(ns cog.agents.guestflow
  (:require [cog.handlers.core :as core]
            [cog.handlers.domain :as d]))

(defn stack [{:keys [tenant-id]}]
  (-> core/raw
      core/rate-limit
      core/retry
      core/cache
      (d/tenant-isolate tenant-id)     ;; hard cross-tenant boundary
      (d/pii-redact {:schema :hotel-guest})
      (d/staff-authorize {:hotel tenant-id})
      (core/policy (core/load-policy :guestflow tenant-id))
      core/cost-budget
      core/audit))
```

Avoids coupling: handlers see only ops and next handler down; stack function is the only place agent-specific composition lives.

## 6. Determinism for replay

Replay impossible unless every non-deterministic input flows through an effect:

- **Time**: ban `System.currentTimeMillis`; everyone uses `:clock/now`. Replay handler returns pre-recorded timestamps.
- **Randomness**: `:random` takes `:kind` (`:uuid`, `:gaussian`, `:seed`) so replay returns exact value stored in audit.
- **External IO** (`:net/fetch`, `:llm/call`, `:tool/call`): recorded by `audit` with request+response. Replay-mode `cache-handler` returns recorded response by content-addressed key.

Replay engine installs stack identical to prod except bottom three swapped:
```
audit → policy → [replay-intercept] → [recorded-cache] → [clock-replay + random-replay] → raw-deny
```

`raw-deny` errors on any un-recorded op — guarantees replayed run cannot side-effect real world. LLM non-determinism handled the Pangolin way: `:llm/call` returns recorded `:fingerprint` (model + system prompt hash + seed); replay fails loudly if fingerprints don't match.

**Audit entry schema** (bitemporal, plugs into Fact Module):

```edn
{:audit/id         #uuid "..."
 :audit/run-id     #uuid "..."
 :audit/parent     #uuid "..."          ;; causal tree, not flat log
 :audit/op         :llm/call
 :audit/args       {...}                ;; canonical, redacted
 :audit/args-hash  "sha256:..."
 :audit/verdict    :allow
 :audit/policy-id  :bankability-v3
 :audit/result     {...}
 :audit/latency-ms 412
 :audit/cost       {:units 0.0031 :currency :usd}
 :audit/valid-from #inst "..."          ;; event time
 :audit/recorded-at #inst "..."         ;; system time
 :audit/handler-chain [:audit :policy :cache :retry :rate-limit :raw]
 :audit/principal  {:agent :bankability :tenant "..." :user "..."}
 :audit/prev-hash  "sha256:..."}        ;; append-only Merkle chain
```

## 7. Concrete Mappings per Project

**BankabilityAI** — `regulator-audit-handler` (S3 Object Lock, Merkle-chained) + `decision-policy-handler` blocking `:decide` without `:rationale` and requiring ARIS 4-reviewer signature. Stack: `regulator-audit → policy → dry-run → cache → retry → rate-limit → raw`.

**Adaptive Trader v2** — `kill-switch` (reads single file; flip to halt; above `audit` so halt is cheap), `position-limit` ($400 USDT cap), `aris-gate` (`:decide :trade-entry` routes through second LLM, different provider), `dry-run` (force during paper trading; PF 0.43 run is reference replay).

**Insurance Comparator** — `pii-redact` before outbound `:net/fetch` (Wakam/Seyna, OGGO); `mia-compliance` denies advice ops unless principal has `:clearance :mia-licensed`; `lead-routing` as EDN, one rule per vertical; `data-residency` forces `:mem/write` of EU PII into Frankfurt.

**GuestFlow** — `tenant-isolate` (injects `WHERE tenant_id = ?`; missing tenant-id denied — class of bug causing Simo v2 regressions), `pii-redact` schema `:hotel-guest` before WhatsApp outbound, `staff-authorize` consulting hotel staff list. Regression-replay suite: record each Simo bug once, gate PRs on replay producing identical handler-chain decisions.

**ModelForge** — `assumption-audit` on `:mem/write` tier `:modelforge-assumption`, before/after + principal. `parameter-validation` denies writes violating sector templates. `artifact-provenance`: every `:emit-artifact` embeds audit run-id in xlsx/pdf metadata.

## 8. Prototype (Python, 118 lines)

```python
# cog_runtime.py — 3 handlers (audit, retry, rate-limit) around :llm/call
import time, json, hashlib, uuid, random, threading
from dataclasses import dataclass, field
from typing import Callable, Any

# ---- Effect primitive --------------------------------------------------
class Effect:
    def __init__(self, op: str, args: dict): self.op, self.args = op, args

def perform(op, **args):
    return _yield(Effect(op, args))

_stack: list = []

def _yield(eff):
    for h in reversed(_stack):
        if eff.op in h.wraps:
            return h.invoke(eff.op, eff.args, _continue, h.ctx)
    raise RuntimeError(f"unhandled effect {eff.op}")

def _continue(value): return value

@dataclass
class Handler:
    name: str
    wraps: set
    ctx: dict = field(default_factory=dict)
    clauses: dict = field(default_factory=dict)
    def invoke(self, op, args, k, ctx):
        return self.clauses[op](args, k, ctx)

# ---- audit handler -----------------------------------------------------
_audit_log = []
def audit_clause(args, k, ctx):
    rid, parent = str(uuid.uuid4()), ctx.get("parent")
    entry = {"id": rid, "parent": parent, "op": "llm/call",
             "args_hash": hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest(),
             "recorded_at": time.time()}
    ctx["parent"] = rid
    t0 = time.time()
    try:
        result = k(args)
        entry["latency_ms"] = int((time.time()-t0)*1000)
        entry["verdict"] = "ok"
        entry["result_hash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result
    except Exception as e:
        entry["verdict"] = "error"; entry["error"] = str(e); raise
    finally:
        entry["prev_hash"] = _audit_log[-1]["id"] if _audit_log else None
        _audit_log.append(entry)

# ---- retry handler -----------------------------------------------------
def retry_clause(args, k, ctx):
    max_attempts, backoff = ctx.get("max", 3), ctx.get("backoff", 0.5)
    for attempt in range(max_attempts):
        try:
            return k(args)
        except TransientError as e:
            if attempt == max_attempts - 1: raise
            time.sleep(backoff * (2 ** attempt) + random.random() * 0.1)

# ---- rate-limit handler (token bucket) ---------------------------------
_rl_lock = threading.Lock()
def rl_clause(args, k, ctx):
    with _rl_lock:
        now = time.time()
        ctx["tokens"] = min(ctx["capacity"],
                            ctx["tokens"] + (now - ctx["last"]) * ctx["refill_per_sec"])
        ctx["last"] = now
        if ctx["tokens"] < 1:
            sleep = (1 - ctx["tokens"]) / ctx["refill_per_sec"]
        else:
            ctx["tokens"] -= 1; sleep = 0
    if sleep > 0: time.sleep(sleep)
    return k(args)

# ---- raw handler (fake LLM) --------------------------------------------
class TransientError(Exception): ...
_call_n = {"n": 0}
def raw_clause(args, k, ctx):
    _call_n["n"] += 1
    if _call_n["n"] % 4 == 0: raise TransientError("vendor 503")
    return {"text": f"echo:{args['messages'][-1]['content']}", "usage": {"tokens": 12}}

# ---- compose + run -----------------------------------------------------
def build_stack():
    _stack.clear()
    _stack.extend([
        Handler("raw",       {"llm/call"}, {}, {"llm/call": raw_clause}),
        Handler("rate-limit",{"llm/call"}, {"tokens":2,"capacity":2,"refill_per_sec":1,"last":time.time()}, {"llm/call": rl_clause}),
        Handler("retry",     {"llm/call"}, {"max":3,"backoff":0.2}, {"llm/call": retry_clause}),
        Handler("audit",     {"llm/call"}, {"parent": None},        {"llm/call": audit_clause}),
    ])

if __name__ == "__main__":
    build_stack()
    for i in range(6):
        out = perform("llm/call", model="claude-opus-4-7", messages=[{"role":"user","content":f"msg{i}"}])
        print(i, out)
    print(f"\naudit entries: {len(_audit_log)}")
```

## 9. Anti-patterns

1. **Side effects in handlers that aren't the raw handler.** `audit` writing to disk synchronously breaks replay determinism. Fix: `audit` emits `:audit/emit` effect drained by a *named* handler at bottom.
2. **Hidden global state.** Each handler instance carries its own `ctx`.
3. **Wall-clock reads outside `:clock/now`.** One `time.time()` in agent code and replay diverges silently. Lint for it.
4. **Deep try/except swallowing effects.** Business code catching exceptions across handler boundary leaks continuations. Reserve exceptions for raw handler; use effect returns for recoverable cases.
5. **Non-canonical args in cache/audit keys.** Unsorted JSON → hash drift → cache misses → replay fails. Always canonicalize.
6. **Handlers that call LLM to make policy decision.** Creates loop. If LLM-in-the-loop policy needed, use *masked* sub-handler so inner `:llm/call` doesn't re-trigger policy.
7. **Mutating EDN policy at runtime.** Policies immutable, version-pinned, swapped atomically. Regulators need to know exactly which policy version produced each decision.
8. **Dry-run handler sharing state with production.** `:mode :dry-run` must route all stateful ops to isolated shadow store.

## What ships in a week

- Day 1: Effect protocol + handler stack runtime, 150 LOC.
- Day 2: Audit + hash-chained log + replay recorder, 100 LOC.
- Day 3: EDN policy evaluator + 10 test policies, 200 LOC.
- Day 4: PII redact + tenant-isolate + rate-limit + retry + cache handlers.
- Day 5: Port GuestFlow (tenant-isolation = cleanest win).
- Day 6: Replay engine + one regression from `simo-feedback-v2`.
- Day 7: `cost-budget` + kill-switch for Trader v2; gate go-live on three successful replays of NO-GO run with new safety stack saying "deny."

## Sources

- Pangolin / LMPL 2025 — handler shape, selection-monad for multi-sample LLM ops
- Composable Effect Handling — arXiv:2507.22048
- Koka book — koka-lang.github.io (§2.3 handlers, §3.4.7 masking, §3.4.13 named/scoped)
- OPA/Rego, AWS Cedar — motivated policy-as-data
- NVIDIA NeMo Guardrails, LLM Guard, Rebuff — prior art for LLM firewalls
- MCP permission model
- Datomic / XTDB bitemporal audit conventions
