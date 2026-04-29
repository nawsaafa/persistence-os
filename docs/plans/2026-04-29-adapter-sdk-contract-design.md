# Adapter SDK Contract — Design

**Date:** 2026-04-29
**Status:** DRAFT. Open for ARIS R1 review before implementation.
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering — Phase 1 substrate-completion stream
**Predecessors:**
- [`2026-04-27-persistence-os-v1.0-roadmap.md`](2026-04-27-persistence-os-v1.0-roadmap.md) — v1.0 ferrari-first roadmap
- [`2026-04-28-v0.7.0a1-module-7-repl-design.md`](2026-04-28-v0.7.0a1-module-7-repl-design.md) — Module 7 REPL (token / capability / WS / JSON-RPC primitives this stream extends)
- conductor track `persistence-os-product_20260429/STATUS.md` — Phase 1 substrate-completion plan, this stream is item #1
- existing module surfaces: `src/persistence/{fact,effect,plan,replay,txn,spec,repl}/__init__.py`
- pyproject.toml license: **AGPL-3.0-or-later** — load-bearing for the open-core decision
**Target tag:** `v0.8.0` (cumulative substrate-completion alpha; bumped from `v0.7.0a1` after Phase 1 streams land)
**Target branch:** `feat/v0.8-adapter-sdk` cut from `main` after the Phase 0 merge train completes
**Window:** 2026-04-30 → 2026-05-04 (5 days for design + impl + ARIS R1+R2)

---

## 1. Summary

Persistence OS today is a Python library: you `import persistence` and you get `DB`, `EffectStack`, `Plan`, `mcts_search`, `make_audit_handler`, `Ref`, `dosync`, etc. — each module has a hand-curated public surface in its own `__init__.py`. There is **no top-level facade**, **no out-of-process wire protocol** outside Module 7 REPL's bespoke ops, and **no standardized way for an external agent (LangChain, OpenAI Assistants, MCP-speaking LLM, another language) to consume the substrate**. The pivot to ferrari-first commercial demands an **adapter contract** — a stable, documented surface that external integrators can build against and that we can keep stable across substrate versions.

This design ships three artifacts:

1. **`persistence.sdk`** — a small in-tree facade module that re-exports the load-bearing surface from each Module under a single import (`from persistence.sdk import Substrate`). Not a wrapper; not a god-object. A curated namespace + 4 lifecycle helpers (`open`, `close`, `version_info`, `health_check`) and a stability annotation system that flags which symbols are "stable v0.8" vs "experimental."
2. **`persistence.sdk.mcp`** — a first-party MCP server that exposes 6 memory + audit tools to any MCP-speaking LLM agent (Claude Desktop, Cursor, Cline, Continue, generic). Tools: `remember`, `recall`, `forget`, `audit_window`, `replay_byte_identity`, `branch_at`. Built on top of Module 7 REPL's capability + token + JSON-RPC envelope (no new auth surface).
3. **Adapter contract spec doc** — `docs/spec/adapter-contract-v0.8.md` — the stability promise: which symbols an adapter may bind to, what versioning guarantees we make, what is allowed to break across versions and what is not. Mirrors the protocol-version-discriminator pattern Module 7 REPL already uses for handshake.

All three preserve the existing AGPL-3.0-or-later license. The MCP server is **AGPL-licensed by default**; commercial customers who do not want AGPL distribution may take a paid commercial license once the company is incorporated. The adapter contract spec is content-addressed and embedded in the `:sdk/contract-version` audit attribute so any session emits provenance for which contract revision it ran under.

**Not** in this stream: LangChain adapter, OpenAI Assistants adapter, multi-language client (TypeScript / Go / Rust). Those land in Phase 2 once the in-tree contract has 2-3 weeks of dogfooding.

**Ships:** ~600 LOC + ~150 tests + spec doc. **5 days.**

---

## 2. Strategic frame

### Why this stream ships first in Phase 1

Three reasons, in order of weight:

1. **It defines the contract every later stream binds to.** Postgres SERIALIZABLE (#137) ships a new `Store` implementation that adapters need to opt into without changing import sites. Plan Edit API (#140) ships new public symbols that need stability annotations. Plan `:code` sandbox (#141) introduces a new effect that adapters must declare a capability for. fold() (#145) adds a Plan AST primitive that the SDK must surface. **Without the SDK contract, each later stream has to invent its own opt-in.** Sequencing this stream first amortizes that across the four downstreams.
2. **It de-risks the coding agent MVP build (Phase 2).** The `persistence-coder` agent we'll build in Phase 2 IS the first SDK consumer. If we discover the SDK shape only when building the agent, we conflate substrate-API design with agent-product design and ship a leaky abstraction. Doing the SDK first forces us to define "what does an external integrator need" before we know the answer from a single use case.
3. **It is the **artifact** Chris (and any first investor conversation) reads.** A pitch that says "Persistence OS is a substrate" is one slide. A pitch that says "any agent imports `persistence.sdk.mcp` and gets bitemporal audit + replay + skill library + STM in 10 lines" is a demo. The MCP server especially is a 10-minute build for any potential design partner — they wire it into Claude Desktop and immediately see audit+replay on their existing agent's traffic.

### Where this stream sits in the v1.0 product roadmap

```
Phase 0 Strategic prep (week 0) ──┬─→ Phase 1 Substrate completion ──┬─→ Phase 2 Coding agent MVP
                                  │      │                            │
                                  │      ├─ ADAPTER SDK [this stream] ┼─ persistence-coder builds against SDK
                                  │      ├─ Postgres SERIALIZABLE     │
                                  │      ├─ Plan Edit API             │
                                  │      ├─ Plan :code sandbox        │
                                  │      └─ fold() executor           │
                                  │                                   │
                                  └─→ Chris brief uses MCP demo ──────┘
```

This stream is the load-bearing **first** Phase 1 item because every other Phase 1 item conforms to its public-surface decisions.

### What this stream preserves (zero canonical-form change)

- All Module-level `__init__.py` files preserve their current exports unchanged. `persistence.sdk` is **additive**; nothing is moved.
- `PLAN_CANONICAL_VERSION = 1` stays at 1.
- `Datom.a` namespacing convention preserved (`sdk/...` follows `repl/...`, `mcts/...`, `skill/...` precedent).
- Audit-chain hash continuity preserved — any SDK call that emits a datom emits via the existing `make_audit_handler` factory; SDK never bypasses the chain.
- Module 7 REPL's capability + token + JSON-RPC envelope is reused; the MCP server registers itself as a REPL "operator session" with a curated capability set.
- AGPL-3.0-or-later licensing on all SDK code.
- TDD strict, canonical JSON, no `time.time()` / `random.random()` in handler code.

### What this stream changes

- New top-level `persistence.sdk` package introduces ONE new public symbol — `Substrate` — that is a **lightweight protocol-style facade** over the existing 7 modules. Not a god-object; documented as a curated namespace.
- New `persistence.sdk.mcp` sub-package ships a runnable MCP server (`python -m persistence.sdk.mcp`).
- New `docs/spec/adapter-contract-v0.8.md` defines the stability surface explicitly — the first time we have one in writing.
- Bumps version `0.7.0a1` → `0.8.0` (no `a1` because by SDK-stream-end the substrate is feature-complete-for-v1.0 except Postgres / Plan Edit / sandbox / fold which are additive and don't break this contract).

---

## 3. Scope

One new sub-package under `src/persistence/sdk/` with five new internal files, one new spec doc, plus extensions to the root `__init__.py`.

| # | File | Public surface | Status |
|---|---|---|---|
| 1 | `src/persistence/sdk/__init__.py` | `Substrate` + lifecycle helpers + stability annotations | NEW |
| 2 | `src/persistence/sdk/_facade.py` | `Substrate` class — curated namespace over the 7 Modules | NEW |
| 3 | `src/persistence/sdk/_stability.py` | `@stable("v0.8")` / `@experimental` / `@deprecated("v0.9", "use Y")` decorator system | NEW |
| 4 | `src/persistence/sdk/_health.py` | `health_check()`, `version_info()`, `module_status()` | NEW |
| 5 | `src/persistence/sdk/mcp/__init__.py` | re-exports + `__all__` | NEW |
| 6 | `src/persistence/sdk/mcp/_server.py` | MCP server: 6 tool definitions + JSON-RPC dispatch | NEW |
| 7 | `src/persistence/sdk/mcp/_tools.py` | `remember`, `recall`, `forget`, `audit_window`, `replay_byte_identity`, `branch_at` — pure functions over `(Substrate, args) → Result` | NEW |
| 8 | `src/persistence/sdk/mcp/__main__.py` | `python -m persistence.sdk.mcp` runner | NEW |
| 9 | `docs/spec/adapter-contract-v0.8.md` | stability surface spec; ~250 lines | NEW |
| 10 | `src/persistence/__init__.py` | extend module list to include `sdk`, bump version `0.7.0a1` → `0.8.0` | EXTENDED |

**Module surface placement.** Sub-package `persistence.sdk/` mirrors the layout of `persistence.repl/` — private leading-underscore files, public surface assembled in `__init__.py`. The MCP server lives at `persistence.sdk.mcp/` so it ships inside the wheel but is logically separable; users who don't want MCP simply don't import it. Zero new external dependencies for the core SDK; the MCP server reuses `aiohttp` already declared in `[project.optional-dependencies].repl`.

### Out of scope (explicit deferrals)

- **LangChain adapter / OpenAI Assistants adapter** — Phase 2 once the in-tree contract has dogfooding signal from `persistence-coder`.
- **Non-Python clients (TypeScript / Go / Rust SDKs)** — Phase 3. Non-Python integrators in v0.8 use the MCP wire protocol (already JSON-RPC over stdio or WS).
- **Persistent SDK sessions across process restart** — Phase 3. SDK sessions live in process memory; underlying datoms persist via the fact store.
- **Per-tenant resource quotas** — covered by capability tokens already (Module 7 REPL gives us per-token rate limiting). Quota enforcement is a Phase 3 multi-tenant ops concern.
- **Telemetry / observability** — emit `:sdk/...` audit attrs but no Prometheus / OpenTelemetry export. Phase 3.

---

## 4. The `Substrate` facade — concrete shape

```python
# src/persistence/sdk/_facade.py
from __future__ import annotations
from typing import Any, Optional
from persistence.fact.db import DB
from persistence.effect.runtime import Runtime
from persistence.effect.handlers.audit import make_audit_handler
# (...other module imports as needed for the curated surface)


class Substrate:
    """Curated namespace + lifecycle for all 7 Persistence OS modules.

    Stable surface as of v0.8 (see ``docs/spec/adapter-contract-v0.8.md``).
    Symbols not on this class are NOT part of the v0.8 stability promise.

    Lifecycle:
        s = Substrate.open(store="memory")  # or "sqlite:///path", "postgres://..."
        ...
        s.close()

    Or as a context manager:
        with Substrate.open() as s:
            ...

    Module access:
        s.fact      # DB instance
        s.effect    # Runtime instance with audit handler pre-installed
        s.plan      # plan parse / execute / mcts_search / promote namespace
        s.replay    # replay engine namespace
        s.txn       # txn primitives namespace
        s.spec      # spec primitives namespace
        s.repl      # REPL server factory (does not auto-start)

    Lifecycle helpers (DO NOT subclass):
        s.health_check()  -> dict — returns module status + suite-version pin
        s.version_info()  -> dict — substrate version + per-module versions
        s.module_status() -> dict — same as repl/inspect "module-status"

    The Substrate class is intentionally THIN. Anything more complex than
    namespace + lifecycle should be done against the underlying Module.
    """

    _stability_version = "v0.8"

    def __init__(self, *, _db: DB, _runtime: Runtime) -> None:
        # Private constructor; use Substrate.open() instead.
        self._db = _db
        self._runtime = _runtime
        self._closed = False

    @classmethod
    def open(
        cls,
        *,
        store: str = "memory",
        capabilities: Optional[set[str]] = None,
        audit: bool = True,
    ) -> "Substrate":
        ...

    def close(self) -> None:
        ...

    def __enter__(self) -> "Substrate": ...
    def __exit__(self, *_: Any) -> None: ...

    @property
    def fact(self) -> DB: ...
    @property
    def effect(self) -> Runtime: ...
    # ... (plan / replay / txn / spec / repl as namespaces)

    def health_check(self) -> dict[str, Any]: ...
    def version_info(self) -> dict[str, str]: ...
    def module_status(self) -> dict[str, dict[str, Any]]: ...
```

The `Substrate` is intentionally **not** a god-object; it is a curated namespace. ADR-1 below pins this decision.

---

## 5. The MCP server — concrete shape

The MCP server runs as `python -m persistence.sdk.mcp [--store URI] [--token TOKEN]`. It registers a curated set of 6 tools an LLM can call and a 1 resource an LLM can read.

### Tools (LLM → server)

| Tool | Capability | Semantics | Audit |
|---|---|---|---|
| `remember(content, tags?)` | `mcp.remember` | Stores `content` as a datom under `e=auto-uuid`, `a=mcp/content`. Returns datom id. | 1 `:mcp/op-remember` audit entry |
| `recall(query, k=5)` | `mcp.recall` | Reads matching datoms via `db.entity(eid)` lookups + tag filter. Returns top-k by recency. **No vector search in v0.8** — substring + tag only. | 1 `:mcp/op-recall` audit entry |
| `forget(eid)` | `mcp.forget` | Retracts the open `mcp/content` assertion (sets `valid_to`). | 1 `:mcp/op-forget` audit entry |
| `audit_window(from_tx, to_tx)` | `mcp.audit-read` | Returns all audit entries in window. | 1 `:mcp/op-audit-window` audit entry |
| `replay_byte_identity(tx)` | `mcp.replay` | Re-runs the replay engine against a given tx and returns byte-identity verification result. | 1 `:mcp/op-replay` audit entry |
| `branch_at(tx, label?)` | `mcp.branch` | Forks the substrate at `tx`. Returns branch id. | 1 `:mcp/op-branch` audit entry |

### Resource (server → LLM)

| Resource | URI | Content |
|---|---|---|
| `audit_tail` | `persistence-os://audit/tail` | Live audit-chain projection (last N entries by default) — server-pushed when subscribed |

### Wire protocol

MCP standard JSON-RPC 2.0 over stdio (default for Claude Desktop / Cursor / Cline) OR over WebSocket (reuses Module 7 REPL `_ws.py` for HTTP-served deployments). Capability tokens issued via `python -m persistence.sdk.mcp --mint-token --capabilities mcp.remember,mcp.recall,...` follow the existing Module 7 token ceremony.

### Why MCP first (not REST / not gRPC / not LangChain)

- **MCP is the de-facto agent-tool protocol.** Anthropic specced it; OpenAI / Cursor / Cline / Claude Desktop / many LLM clients support it. Targeting it first reaches the most agents per LOC.
- **Stdio transport is zero-config** for desktop agents. WS transport reuses our existing REPL server.
- **Schema is minimal.** 6 tools + 1 resource fits in <250 LOC.
- **Versioning is built in.** MCP includes a protocol-version field at handshake; we declare `persistence-os/v0.8`.

---

## 6. ADRs

### ADR-1: `Substrate` is a curated namespace, not a wrapper

**Decision.** The `Substrate` class exposes the existing Module surfaces via attribute access (`s.fact`, `s.effect`, etc.). It does NOT wrap, proxy, or re-implement any Module method. Adapter authors who need fine control reach through to the underlying Module (`s.fact.transact(...)`).

**Rationale.** Wrapping creates two parallel APIs that drift over time (Module-direct vs Substrate-mediated). A facade that adds `__enter__/__exit__` + a curated attribute namespace + 3 lifecycle helpers is enough to give adapters a stable opening hook without sacrificing direct-Module ergonomics. Three real wrappers we considered and rejected: a `transact()` method on Substrate (would compete with `s.fact.transact`), a `remember()` method on Substrate (would compete with the MCP layer's `remember` tool), an `audit()` method on Substrate (would compete with the existing audit-handler factory).

**Consequence.** Stability promises live in `_stability.py`'s decorators applied to the Module-level symbols, not on the Substrate class. The Substrate's job is namespace + lifecycle, full stop.

### ADR-2: MCP server ships in-tree, not as a separate package

**Decision.** `persistence.sdk.mcp` is a sub-package of the main `persistence` distribution. There is no separate `persistence-mcp` PyPI package in v0.8.

**Rationale.** (a) The server is small (~250 LOC) and depends on substrate internals. (b) Splitting at v0.8 means version-skew bugs surface immediately (an MCP server pinned to one substrate version will silently break against another). (c) Python users `pip install "persistence[mcp]"` to opt in; the optional-dependency machinery handles the `aiohttp` extra.

**Consequence.** If we ship a non-Python client later (Phase 3), THAT will be a separate package. v0.8 is Python-only.

### ADR-3: Reuse Module 7 REPL's capability + token system; do not invent a new auth surface

**Decision.** The MCP server runs as a "REPL operator session" with a curated capability set (`mcp.remember`, `mcp.recall`, etc.). Token minting + revocation use the existing `mint_token` / `revoke_token` from `persistence.repl._caps`.

**Rationale.** Module 7 already has a tested capability + token + audit-emission stack with R2 PASS. Building a parallel auth system would duplicate ~400 LOC of token-validation + capability-check code. The MCP capabilities are a new namespace (`mcp.*`) but use the same `Capability(op, qualifier)` tuple shape.

**Consequence.** The `persistence.repl._caps.QUALIFIERS_BY_OP` table is extended with the 6 MCP qualifiers; no other change to Module 7. Token lifecycle is unchanged: bootstrap CLI mints, env-var loads, fact-store persists.

### ADR-4: Audit chain uses `:mcp/...` namespace; no new prev-hash chain

**Decision.** All MCP-emitted datoms use `Datom.a = "mcp/op-{remember,recall,forget,audit-window,replay,branch}"`. The `prev_hash` field chains into the same root audit chain as `:audit/`, `:tx/`, `:repl/`, `:mcts/`, etc.

**Rationale.** Module 7 set the precedent — REPL ops chain into the same audit chain rather than forking a per-Module chain. MCP follows. A reviewer asking "does this session have any unaudited MCP traffic" gets a single chain to walk, not a graph of chains.

**Consequence.** `verify_chain()` (from `persistence.effect.handlers.audit`) works unchanged on a session with MCP traffic. No schema change.

### ADR-5: Stability annotations are a hard contract; v0.8 → v0.9 breaks emit a `:sdk/contract-version` mismatch warning

**Decision.** Every symbol re-exported under `Substrate` (or directly callable via the SDK contract spec) is decorated with `@stable("v0.8")` or `@experimental` or `@deprecated("v0.9", "use Y")`. Calling a `@deprecated` symbol emits a Python `DeprecationWarning` AND writes a `:sdk/deprecated-call` audit entry. Calling an `@experimental` symbol emits no warning but is documented as breakable.

**Rationale.** Adapter authors need a machine-readable contract. A decorator system gives us (a) introspection (`SDK.list_stable()`), (b) audit emission for deprecation tracking, (c) documentation generation hooks for the spec doc. Three alternatives considered and rejected: docstring-only conventions (machine-unreadable), per-module YAML manifests (drift-prone), separate `__stable__` exports (harder to enforce).

**Consequence.** v0.8 ships with ~60 symbols marked `@stable("v0.8")` and ~15 marked `@experimental`. The spec doc is generated from the decorator metadata, so doc + code can't drift.

### ADR-6: License model — substrate stays AGPL; MCP server stays AGPL; commercial license is a future SKU

**Decision.** Substrate, SDK facade, MCP server: all AGPL-3.0-or-later in v0.8. Commercial dual-license offering is a Phase 4 (post-incorporation) decision and is NOT promised in v0.8.

**Rationale.** AGPL is the strongest copyleft and ensures any networked SaaS use of the substrate triggers source-disclosure. This is the right default for a frontier-positioned infrastructure project — it discourages cloud providers from white-labeling without contributing back. Commercial customers who need a non-AGPL license will negotiate one once the company is incorporated.

**Consequence.** README / pyproject / spec doc all carry an AGPL banner. The Chris-brief explicitly notes the AGPL choice and frames it as the open-core positioning.

### ADR-7: Adapter contract version pins to substrate version (not SDK version)

**Decision.** The adapter contract version is the substrate version (`v0.8`). There is no separate SDK semver. An adapter author pins their code to "Persistence OS v0.8 contract" and we promise that any `0.8.x` patch release preserves the contract.

**Rationale.** Two version strings (substrate + SDK) compound surface. One version string keeps the mental model simple for adapter authors. Contract-breaking changes can only happen at minor-version bumps (`0.8 → 0.9`) — patch bumps are bug-fix-only.

**Consequence.** When we ship `0.8.1` (e.g. closing a bug found by `persistence-coder`), the contract is unchanged; adapter authors do not re-test. When we ship `0.9.0`, all contract-breaking deprecations announced in `0.8.x` go live.

### ADR-8: MCP `recall` tool is substring + tag based in v0.8; vector search deferred

**Decision.** `recall(query, k=5)` performs case-insensitive substring matching on `mcp/content` values + tag filtering. No vector embeddings, no semantic search, no LLM-mediated relevance scoring.

**Rationale.** v0.8 ships in 5 days. Vector search needs an embeddings adapter (which embedding model? hosted vs local? what dimensions? per-tenant or shared?), a vector index (qdrant? duckdb-vss? in-memory?), and a recall-quality eval harness. None of those are in scope for v0.8. Substring + tag is good enough for the coding-agent MVP demo (the "what did I store about feature X" query).

**Consequence.** Vector search is `v0.9` Phase 2 work. The decorator marks `recall` as `@stable("v0.8", "substring+tag mode")` so v0.9's vector-search version is a NEW symbol (`recall_semantic` or similar), not a breaking change to the existing one.

### ADR-9: Substrate.open(store="...") accepts a URI; backends are pluggable

**Decision.** `Substrate.open(store="memory")` / `Substrate.open(store="sqlite:///path/to/db")` / `Substrate.open(store="postgres://user:pass@host/db")`. URI is parsed; backend is loaded from a registered map.

**Rationale.** The Postgres backend (#137) ships in Phase 1 alongside this stream; using a URI from day one means adopters don't refactor when they migrate from sqlite to postgres. Three backends in v0.8: `memory` (existing), `sqlite` (existing), `postgres` (Phase 1 #137 — depends on optional `[postgres]` extra).

**Consequence.** The URI parser is a 30-line addition; the backend dispatch is a `match` statement. The `Substrate.open()` keyword `store` is a positional convenience that maps to `Store.open(uri=...)` under the hood.

### ADR-10: First-party adapters in v0.8 = Python SDK + MCP. LangChain / OpenAI Assistants are Phase 2.

**Decision.** v0.8 ships exactly two adapters: the Python SDK facade and the MCP server. LangChain Tool wrappers, OpenAI Assistants Tool wrappers, and the DSPy adapter (which exists internally but is not packaged for external consumption) are Phase 2 follow-ups.

**Rationale.** The Phase 2 coding agent MVP IS the dogfooding venue for the SDK. Building three more adapters in v0.8 adds surface area without adopters, and the right shape of those adapters depends on what the coding-agent build reveals about the SDK ergonomics.

**Consequence.** The spec doc explicitly lists "first-party in v0.8: SDK + MCP. Phase 2 adds LangChain Tool, OpenAI Assistants Tool, DSPy adapter packaging." Third-party adapters can build against the v0.8 contract today; they take the stability promise on contract symbols only.

### ADR-11: Health check returns module-by-module status with audit-chain integrity probe

**Decision.** `Substrate.health_check()` returns a dict: per-module load status, substrate-version, audit-chain head hash + verify_chain result on a small window, current store backend, current open transactions count, current REPL session count. **The audit-chain probe is the load-bearing health signal** — if it fails, the substrate is silently corrupted.

**Rationale.** Adapter authors need a single call to know "is this substrate healthy?" Most "is X healthy" calls in software return uselessly green; the audit-chain probe gives a real signal. Cost is low — verify_chain on the last 10 entries is microseconds.

**Consequence.** Adapter authors are documented to call `health_check()` at session open and on-demand. Returns shape is `@stable("v0.8")`. Phase 3 ops dashboards aggregate this output.

### ADR-12: SDK does not auto-start REPL server; opt-in via `Substrate.open(repl=True)` or `s.repl.serve(...)`

**Decision.** Default `Substrate.open()` does NOT start a REPL server. Opt-in is explicit: `Substrate.open(repl=True)` (boots on default port) or `s.repl.serve(host=..., port=...)` (manual control).

**Rationale.** Most SDK consumers (Python apps embedding the substrate) do not want a network-listening REPL server. Default-off is the safe choice. The Phase 2 coding agent will explicitly opt in.

**Consequence.** REPL server lifetime is bound to the Substrate context; `s.close()` shuts down any auto-started REPL.

---

## 7. Acceptance gates (ARIS R1 + R2)

ARIS R1 (this design doc) target: mean ≥ 8.5 / min ≥ 7.5 per the auto-review-loop hard-mode pattern.

ARIS R2 (post-implementation) target: mean ≥ 8.5 / min ≥ 7.5 on the merged branch tip.

### G1 — Substrate facade load test (smoke)

Open `Substrate.open(store="memory")`, exercise each Module's first-line API (e.g. `s.fact.transact([])`, `s.txn.dosync(...)`, `s.plan.parse('...')`, `s.replay.byte_identity(...)`), close. Assert health check returns green throughout.

### G2 — MCP server end-to-end

Start `python -m persistence.sdk.mcp --store memory --port 8765` in a subprocess. Connect a stdio-MCP client; call `remember("hello world", tags=["greeting"])`, then `recall("hello")`, assert the returned list contains the just-remembered datom. Verify a `:mcp/op-remember` and `:mcp/op-recall` audit entry chained correctly.

### G3 — Stability decorator system

`SDK.list_stable()` returns the curated set of `@stable("v0.8")` symbols. Calling a `@deprecated` symbol emits both a Python warning AND a `:sdk/deprecated-call` audit entry. Calling an `@experimental` symbol emits no warning. Calling a `@stable("v0.8")` symbol emits nothing.

### G4 — Audit chain integrity across SDK + MCP traffic

Mix substrate-direct traffic (`s.fact.transact(...)`), Module 7 REPL traffic (`s.repl.serve` + an op), and MCP traffic (`mcp.remember`). Run `verify_chain()` over the entire window. Must return `True` on a clean run. Tampering with one entry must surface as `False`.

### G5 — License banner on every public artifact

`pyproject.toml` license = AGPL-3.0-or-later. Spec doc has AGPL banner. README has AGPL banner. MCP server `--version` output has AGPL banner. Test asserts these are in sync.

### G6 — Backend URI parsing

`Substrate.open(store="memory")` / `sqlite:///tmp/x.db` / `postgres://...` all dispatch to the right backend. `postgres://...` in the absence of the `[postgres]` extra raises `BackendNotInstalled`. Future backends register via a plugin point.

### G7 — Spec doc generated from decorators

Run a doc-generation script (`scripts/gen_adapter_contract.py`) that reads decorator metadata from the SDK and emits `docs/spec/adapter-contract-v0.8.md`. Assert the committed spec doc and the generated output match (CI gate).

---

## 8. Task breakdown (subagent dispatch model)

Per the v0.5-txn Phase B / v0.6.0a1 / v0.6.5 / v0.7.0a1 precedent: per-task subagent dispatch with two-stage review (implementer + spec-reviewer + code-quality-reviewer). 5 tasks + integration + ARIS gates.

| Task | Scope | Estimated LOC + tests |
|---|---|---|
| **SDK1** — `_facade.py` + `Substrate` class + `_stability.py` decorators | core SDK + decorator system + ~20 unit tests | ~250 / ~80 |
| **SDK2** — `_health.py` + `health_check`, `version_info`, `module_status` + ~10 tests | health probes | ~120 / ~40 |
| **SDK3** — `mcp/_server.py` + `mcp/_tools.py` + 6 tools + JSON-RPC dispatch + ~30 tests | MCP server core | ~250 / ~120 |
| **SDK4** — `mcp/__main__.py` runner + token mint CLI + AGPL banner + ~15 tests | runnable artifact | ~80 / ~40 |
| **SDK5** — `docs/spec/adapter-contract-v0.8.md` + `scripts/gen_adapter_contract.py` + CI gate | spec doc generator | ~250 / ~30 |
| **SDK-INT** — end-to-end integration test: open Substrate → mint MCP token → boot MCP server → remember/recall/forget → verify_chain across SDK + REPL + MCP traffic | e2e | ~150 |
| **SDK-FINAL.1** — ARIS R2 code-quality (codex hard-mode) | review | — |
| **SDK-FINAL.2** — CHANGELOG + version bump `0.7.0a1` → `0.8.0` + local tag | release | — |

Total estimated impl: ~1100 LOC src + ~310 tests. 5 days.

---

## 9. Open questions (defer to ARIS R1 review or impl phase)

1. **Should `Substrate` expose a `context` mechanism for per-call request-id / tenant-id propagation?** Alternative: adapter authors handle it via ContextVar at the call site. Recommend: ContextVar at call site for v0.8; revisit if `persistence-coder` needs per-request tagging.
2. **MCP `recall` ranking — recency only, or also a small tag-relevance score?** v0.8 default: recency-only, k=5. Tag relevance is a `recall_with_score(query, tags, k)` separate symbol if needed.
3. **Should the MCP server expose a `subscribe(audit-tail)` resource alongside the 6 tools?** v0.8 default: yes, push-based via WS; stdio transport falls back to poll-based. Audit traffic is the entire moat.
4. **Capability tokens for SDK consumers** — should an in-process `Substrate.open()` consumer also need a token, or is process-local trust sufficient? Recommend: token NOT required for in-process; required for any out-of-process or network-served consumer.
5. **Health-check audit-chain probe window size** — default 10 entries vs default 100 entries vs explicit user-set? Recommend: configurable via `health_check(window=N)`; default 10.

---

## 10. Risks + mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Adapter contract decisions ossify before we know what `persistence-coder` needs | medium | high | ADR-7 — patch versions preserve contract; minor versions allow breaks. Phase 2 dogfooding has a defined "contract amendment" surface in `0.9.0`. |
| R2 | MCP protocol evolves under us mid-Phase 2 | low | medium | Version handshake; ADR-7 substrate-version-pin; spec doc explicitly notes MCP version targeted (current Anthropic spec). |
| R3 | AGPL license discourages adoption | medium | medium | Documented in Chris-brief as a deliberate moat. Commercial dual-license offered post-incorporation. |
| R4 | `Substrate` becomes a god-object over time | medium | medium | ADR-1 hard-pin: facade is namespace + lifecycle, no wrapping. Code-review gate enforces. |
| R5 | Stability decorator drift between code and spec doc | low | high | ADR-5 + G7 — spec doc is generated from decorators; CI gate fails if drift detected. |
| R6 | Audit chain explosion from chatty MCP traffic | medium | low | Per-tool capability limits via Module 7 tokens; rate-limit handler stack already exists. |
| R7 | Token leakage via verbose logs | medium | high | Token IDs (sha256[:16]) logged; raw tokens NEVER. Inherits Module 7 token discipline. |

---

## 11. Persistence

- This design doc: `docs/plans/2026-04-29-adapter-sdk-contract-design.md`
- ARIS R1 review: `review-stage/v0.8-adapter-sdk-r1/`
- Conductor: `persistence-os-product_20260429/STATUS.md` Phase 1 block append
- Vault: `nawfal-dev/L1` topic memory at design-doc-merge + R1-pass
- Serena memory: `v0.8-adapter-sdk-design-locked` post R1 PASS

---

## 12. Recovery instructions for next session

1. Read this doc + the conductor STATUS first.
2. Check ARIS R1 result at `review-stage/v0.8-adapter-sdk-r1/REVIEW.md`. If PASS, proceed; if FAIL, run W1 fix-pass against the listed MAJORs.
3. Branch `feat/v0.8-adapter-sdk` from `main` post-merge-train.
4. Subagent-dispatch SDK1 → SDK5 → SDK-INT → SDK-FINAL.{1,2} per the v0.7.0a1 precedent.
5. On SDK-FINAL.2 PASS: tag `v0.8.0`, append CHANGELOG, persist Serena + vault + auto-memory, conductor STATUS Phase 1 block close.
