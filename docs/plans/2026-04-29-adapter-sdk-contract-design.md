# Adapter SDK Contract — Design

**Date:** 2026-04-29
**Status:** DRAFT. Open for ARIS R1 review before implementation.
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering — Phase 1 substrate-completion stream
**Predecessors:**
- [`2026-04-27-persistence-os-v1.0-roadmap.md`](2026-04-27-persistence-os-v1.0-roadmap.md) — v1.0 ferrari-first roadmap
- [`2026-04-28-v0.7.0a1-module-7-repl-design.md`](2026-04-28-v0.7.0a1-module-7-repl-design.md) — Module 7 REPL (token / capability / JSON-RPC primitives this stream extends; Module 7's WS transport is REPL-only and explicitly NOT used for the v0.8 MCP server — see ADR-15 § 5)
- conductor track `persistence-os-product_20260429/STATUS.md` — Phase 1 substrate-completion plan, this stream is item #1
- existing module surfaces: `src/persistence/{fact,effect,plan,replay,txn,spec,repl}/__init__.py`
- pyproject.toml license: **AGPL-3.0-or-later** — load-bearing for the open-core decision
**Target tag:** `v0.8.0a1` (W1-revised — alpha retained because v0.8 is the first release that ships the public adapter contract and Phase 2 dogfooding will exercise it; non-alpha `v0.8.0` follows 1-2 weeks of `persistence-coder` use)
**Target branch:** `feat/v0.8-adapter-sdk` cut from `main` after the Phase 0 merge train completes
**Window:** 2026-04-30 → 2026-05-04 (5 days for design + impl + ARIS R1+R2)

---

## 1. Summary

Persistence OS today is a Python library: you `import persistence` and you get `DB`, `EffectStack`, `Plan`, `mcts_search`, `make_audit_handler`, `Ref`, `dosync`, etc. — each module has a hand-curated public surface in its own `__init__.py`. There is **no top-level facade**, **no out-of-process wire protocol** outside Module 7 REPL's bespoke ops, and **no standardized way for an external agent (LangChain, OpenAI Assistants, MCP-speaking LLM, another language) to consume the substrate**. The pivot to ferrari-first commercial demands an **adapter contract** — a stable, documented surface that external integrators can build against and that we can keep stable across substrate versions.

This design ships three artifacts:

1. **`persistence.sdk`** — a small in-tree facade module that re-exports the load-bearing surface from each Module under a single import (`from persistence.sdk import Substrate`). Not a wrapper; not a god-object. A curated namespace + 4 lifecycle helpers (`open`, `close`, `version_info`, `health_check`) and a stability annotation system that flags which symbols are "stable v0.8" vs "experimental."
2. **`persistence.sdk.mcp`** — a first-party MCP server that exposes 6 memory + audit tools to any MCP-speaking LLM agent (Claude Desktop, Cursor, Cline, Continue, generic). Tools (host-name-collision-safe `persistence_` prefix): `persistence_remember`, `persistence_recall`, `persistence_forget`, `persistence_audit_window`, `persistence_replay_check`, `persistence_view_at`. (`replay_byte_identity` renamed to `replay_check` because the public output is a boolean + reason code, not a byte diff — see ADR-13 § 6 below; `branch_at` renamed to `view_at` because per Module 7 ADR-13 our "branch" is a cursor + depth marker, NOT a store fork — see ADR-14 § 6 below.) Tool envelope conforms to MCP server tools spec (`inputSchema` / optional `outputSchema` / `structuredContent` / `isError`); see § 5.2 + ADR-15. Built on top of Module 7 REPL's capability + token system (no new auth surface).
3. **Adapter contract spec doc** — `docs/spec/adapter-contract-v0.8.md` — the stability promise: which symbols an adapter may bind to, what versioning guarantees we make, what is allowed to break across versions and what is not. Mirrors the protocol-version-discriminator pattern Module 7 REPL already uses for handshake.

All three preserve the existing AGPL-3.0-or-later license. The MCP server is **AGPL-licensed by default**; commercial customers who do not want AGPL distribution may take a paid commercial license once the company is incorporated. The adapter contract spec is content-addressed and embedded in the `:sdk/contract-version` audit attribute so any session emits provenance for which contract revision it ran under.

**Not** in this stream: LangChain adapter, OpenAI Assistants adapter, multi-language client (TypeScript / Go / Rust). Those land in Phase 2 once the in-tree contract has 2-3 weeks of dogfooding.

**Ships:** ~1200 LOC src + ~310 tests + spec doc. **5 days.** (Per § 8 task table — the earlier "~600 LOC" R0-draft line pre-dated the spec-doc generator + e2e suite; bumped 1100 → 1200 in W3 to absorb the schema-generator `$ref` inliner per § 5.1.1.)

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
- Bumps version `0.7.0a1` → `0.8.0a1` (W1-revised — `a1` retained because v0.8 is the first release that ships the public adapter contract and Phase 2 dogfooding will exercise it; dropping `a1` is a v0.8.0 gesture that should follow 1-2 weeks of `persistence-coder` usage).

---

## 3. Scope

One new sub-package under `src/persistence/sdk/` with five new internal files, one new spec doc, plus extensions to the root `__init__.py`.

| # | File | Public surface | Status |
|---|---|---|---|
| 1 | `src/persistence/sdk/__init__.py` | `Substrate` + lifecycle helpers + stability annotations + `types` re-export | NEW |
| 2 | `src/persistence/sdk/_facade.py` | `Substrate` class — curated namespace over the 7 Modules; module getters carry `@experimental` marker per ADR-1 W1 | NEW |
| 3 | `src/persistence/sdk/_stability.py` | `@stable("v0.8", note?: str)` / `@experimental` / `@deprecated("v0.9", "use Y")` decorator system per ADR-5 + ADR-16 semantics | NEW |
| 4 | `src/persistence/sdk/_types.py` | TypedDict registry per ADR-16 — every `@stable` dict-returning symbol has a TypedDict here; re-exported as `persistence.sdk.types` | NEW |
| 5 | `src/persistence/sdk/_health.py` | `health_check()`, `version_info()`, `module_status()` | NEW |
| 6 | `src/persistence/sdk/mcp/__init__.py` | re-exports + `__all__` | NEW |
| 7 | `src/persistence/sdk/mcp/_names.py` | `_NAMES` dict — single source of truth for the (wire / capability / Datom.a / AuditEntry.op) tuple per ADR-15 | NEW |
| 8 | `src/persistence/sdk/mcp/_schemas.py` | `inputSchema` + `outputSchema` JSON-Schema dicts for the 6 tools, generated from TypedDicts in `_types.py` | NEW |
| 9 | `src/persistence/sdk/mcp/_server.py` | MCP server: lifecycle (`initialize` → `notifications/initialized` → `tools/list`/`tools/call` / `resources/*`); JSON-RPC dispatch; transport stdio + Streamable HTTP per ADR-15 | NEW |
| 10 | `src/persistence/sdk/mcp/_tools.py` | the 6 tools (`persistence_*`) as pure functions over `(Substrate, args) → Result`; budget enforcement per ADR-13 | NEW |
| 11 | `src/persistence/sdk/mcp/_budgets.py` | `MCP_REPLAY_MAX_WINDOW`, `MCP_REPLAY_MAX_WALLCLOCK_S`, `MCP_REPLAY_RATE_LIMIT_PER_TOKEN` constants per ADR-13 | NEW |
| 11a | `src/persistence/sdk/mcp/_http_security.py` | EXPERIMENTAL Streamable-HTTP transport: bind-loopback enforcement, Origin allowlist + DNS-rebinding mitigation, Bearer-token authn middleware, per-connection session, rate-limit per ADR-15 § 5 + ADR-15b — see G12 | NEW (experimental, not @stable in v0.8) |
| 12 | `src/persistence/sdk/mcp/__main__.py` | `python -m persistence.sdk.mcp` runner (token-file / env-var bootstrap per ADR-15a; `--enable-experimental-http` gate per ADR-15b) | NEW |
| 13 | `docs/spec/adapter-contract-v0.8.md` | stability surface spec; ~300 lines (auto-generated from decorators + TypedDicts + `_NAMES`) | NEW |
| 14 | `docs/spec/adapter-contract-v0.8.lock.json` | machine-readable signature-sha lockfile per ADR-16 + G10b; ~80 lines | NEW |
| 15 | `scripts/gen_adapter_contract.py` | spec + lockfile generator; CI gate per G7 + G10 | NEW |
| 16 | `src/persistence/__init__.py` | extend module list to include `sdk`, bump version `0.7.0a1` → `0.8.0a1` | EXTENDED |

**Module surface placement.** Sub-package `persistence.sdk/` mirrors the layout of `persistence.repl/` — private leading-underscore files, public surface assembled in `__init__.py`. The MCP server lives at `persistence.sdk.mcp/` so it ships inside the wheel but is logically separable; users who don't want MCP simply don't import it. Zero new external dependencies for the core SDK; the MCP server reuses `aiohttp` already declared in `[project.optional-dependencies].repl` for the optional Streamable-HTTP transport (ADR-15). The stdio transport requires no external deps (Python stdlib only).

**Version-bump revision.** Original draft proposed `0.7.0a1 → 0.8.0`. Revised post-R1 to `0.7.0a1 → 0.8.0a1` — the `a1` is retained because v0.8 is the FIRST release that ships the public adapter contract and the contract is by definition still under dogfooding pressure during Phase 2. Dropping `a1` is a v0.8.0 (non-alpha) gesture that should follow at least 1-2 weeks of `persistence-coder` usage. (Per R1 NIT-7.)

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

The MCP server runs as `python -m persistence.sdk.mcp [--store URI] [--token TOKEN] [--transport stdio|http]`. It registers a curated set of 6 tools an LLM can call and 1 resource an LLM can read.

### MCP spec conformance pin

- **Spec revision targeted:** [MCP `2025-06-18`](https://modelcontextprotocol.io/specification/2025-06-18) (current at v0.8 design freeze, 2026-04-29). Wire protocol-version string at handshake: `2025-06-18`. The server may still negotiate `2025-03-26` for older clients (its `initialize` reply selects the highest mutually-supported version per spec §lifecycle).
- **Lifecycle conformance:** `initialize` (client → server, capabilities + protocolVersion + clientInfo) → `result` → `notifications/initialized` (client → server) → ready. Then `tools/list`, `tools/call`, `resources/list`, `resources/read`, `resources/subscribe`. Standard JSON-RPC 2.0 error envelope.
- **Tool naming pin:** every tool name is prefixed `persistence_` for host-aggregator collision safety (per MCP server-tools naming guidance).

### Tools (LLM → server)

All 6 tools publish a JSON-Schema `inputSchema` and (when emitting structured data) an `outputSchema`. Tool results return `structuredContent` (typed) plus a fallback `content[].text` for clients that don't consume structured output. Errors raise via the JSON-RPC envelope OR set `isError: true` on the result depending on the failure class (§ 5.2 below). The `tools/list` reply is the authoritative wire-shape; the table below is a reading aid.

| Tool | Capability (cap-token) | Input (sketch) | Structured output (sketch) | Audit |
|---|---|---|---|---|
| `persistence_remember(content: str, tags?: list[str])` | `mcp.remember` | `{content: str (1..16384), tags?: str[]}` | `{eid: uuid, tx: int, valid_from: iso}` | 1 `:mcp/op-remember` audit entry |
| `persistence_recall(query: str, k?: int = 5, tags?: list[str], cursor?: str)` | `mcp.recall` | `{query: str, k: int 1..50, tags?: str[], cursor?: str}` | `{hits: [{eid, content, tags, valid_from}], next_cursor?: str}` | 1 `:mcp/op-recall` audit entry |
| `persistence_forget(eid: uuid)` | `mcp.forget` | `{eid: uuid}` | `{eid, valid_to: iso, retracted: bool}` | 1 `:mcp/op-forget` audit entry |
| `persistence_audit_window(from_tx: int, to_tx?: int, limit?: int = 100, cursor?: str)` | `mcp.audit-read` | `{from_tx: int ≥ 0, to_tx?: int, limit: int 1..1000, cursor?: str}` | `{entries: [{op, args_hash, result_hash, prev_hash, txn_commit}], next_cursor?: str, head_hash: str}` | 1 `:mcp/op-audit-window` audit entry |
| `persistence_replay_check(tx: int, window?: int = 32)` | `mcp.replay` | `{tx: int, window?: int 1..256}` | `{ok: bool, reason_code: str, window_actual: int, head_hash: str}` — **never returns raw byte diffs** by default; reason codes ∈ `{ok, mismatch_user_log, mismatch_audit_chain, window_too_large, replay_aborted_budget}` (see ADR-13) | 1 `:mcp/op-replay` audit entry |
| `persistence_view_at(tx: int, label?: str)` | `mcp.view` | `{tx: int, label?: str (≤64 chars)}` | `{cursor_id: uuid, view_cursor_tx_time_iso: iso, parent_chain_depth: int, label?: str}` — **cursor handle, NOT a store fork** (see ADR-14 / Module 7 ADR-13) | 1 `:mcp/op-view` audit entry |

### 5.1 Tool envelope details

- **`inputSchema`** is a JSON-Schema document declaring `$schema: "http://json-schema.org/draft-07/schema#"` (W2-revised — see "Schema Profile v0.8" pin below) with `type: object`, `additionalProperties: false`, `required` populated. Schemas are checked by the dispatcher BEFORE any handler runs (`-32602 invalid params` on schema-fail).
- **`outputSchema`** is published for every tool that returns `structuredContent`. The dispatcher validates the structured payload against `outputSchema` before sending — a server-side validator failure surfaces as `isError: true` + reason `internal_output_validation`.
- **`isError: true`** is reserved for *handled* failures the model is meant to see and react to (capability denial, retractable input, budget exhausted). *Unhandled* failures (transport, validation, internal) use the JSON-RPC `error` envelope.
- **Pagination cursors.** `recall` and `audit_window` accept `cursor` and return `next_cursor`. Per W2 NIT-5: cursor format is `base64url(HMAC-SHA256(secret, payload) || payload || version_byte)` where payload is the canonical-JSON of `{last_eid_or_tx: …, issued_at: iso}`. Cursors are **opaque-but-decodable-with-server-secret**: clients must NOT decode them; the HMAC binds them to the server instance so a cursor from one server cannot be replayed against another. Server secret rotates on process restart (cursors do not survive restart, which is acceptable v0.8 behavior — recall + audit_window callers re-issue from cursor=null on reconnection).

### 5.1.1 Schema Profile v0.8 (W2 BLOCKER-2 closure)

MCP client SDKs in the wild have uneven support for JSON-Schema draft-2020-12 features (e.g. `$dynamicRef`, `unevaluatedProperties`, dependent-schemas under nested keywords). To avoid generator-change-as-contract-change drift AND maximize cross-client interoperability, v0.8 generates schemas under a **conservative profile** — the strict subset of [JSON Schema draft-07](https://json-schema.org/draft-07) that `pydantic.TypeAdapter` and `jsonschema-validator>=4` both consume losslessly, intersected with what current MCP clients (Claude Desktop, Cursor, Cline, Continue, openai-agents-python) handle:

**Allowed keywords:** `type`, `properties`, `required`, `additionalProperties` (always `false` at object root), `items`, `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`, `maximum`, `enum`, `const`, `pattern`, `description`, `default`, `examples`. `format` allowed for `uuid` / `date-time` / `uri` only.

**Disallowed keywords (generator rejects with `schema_profile_violation` at startup):** `$ref` (inline only — no schema reuse via `$defs`/`$ref` to keep schemas one-pass-readable for clients), `$dynamicRef`, `$dynamicAnchor`, `if`/`then`/`else`, `dependentSchemas`, `unevaluatedProperties`, `unevaluatedItems`, `not`, `oneOf`/`anyOf`/`allOf` (use `enum` instead — TypedDicts disallowing union types is the upstream constraint), `prefixItems`, custom `format` values beyond the three above.

**Schema lockfile.** `adapter-contract-v0.8.lock.json` includes a `schemas` block with the SHA-256 of each tool's canonical-JSON `inputSchema` AND `outputSchema`:

```json
{
  "schemas": {
    "persistence_remember": {
      "input_schema_sha256": "abc123...",
      "output_schema_sha256": "def456..."
    },
    ...
  }
}
```

CI gate G10b is extended to fail on any schema-SHA diff. A generator change that produces semantically-equivalent but byte-different schemas is THUS a contract change requiring a minor-version bump — the lockfile makes drift detectable, not silently propagated. The generator's profile-violation check guards against accidentally widening the surface.

**TypedDict → JSON Schema generator pin (W3 SHOULD-FIX 1 detail).** Pydantic's `TypeAdapter(TD).json_schema(mode='validation')` emits `$defs` + `$ref` + `title` by default — "strip `$defs`" alone is insufficient because `$ref` pointers into the stripped `$defs` would dangle. The v0.8 generator therefore implements a pre-pass that DEREFERENCES + INLINES every `$ref` against the source `$defs`, then strips the now-empty `$defs` block, then strips emitted `title` fields (which would otherwise add a non-functional surface to the schema and trigger profile-violation if pydantic's title interacts with `examples`). Concretely:

```python
# scripts/gen_adapter_contract.py — schema generator pipeline
def emit_schema(td_class) -> dict:
    raw = pydantic.TypeAdapter(td_class).json_schema(mode='validation')
    # 1. Recursive $ref inliner: walk `raw`; for each {"$ref": "#/$defs/<name>"},
    #    replace the dict containing the $ref with a deep-copy of raw["$defs"][name],
    #    recursing through nested $refs. Cycle detection: refuse + error if a $ref
    #    cycle exists (TypedDicts shouldn't produce these; cycle = bug in TypedDict).
    inlined = inline_refs(raw)
    # 2. Strip metadata keys we don't need: $defs, title, examples-at-non-root.
    stripped = strip_keys(inlined, keys={"$defs", "title"}, preserve_at_root={"examples"})
    # 3. Profile-validate: walk every node; if any disallowed keyword present (per
    #    § 5.1.1 closed list), raise SchemaProfileViolation with the offending path.
    validate_profile(stripped)
    # 4. Sort all dict keys lexically (recursive) for byte-identical output across
    #    Python versions / dict insertion-order changes.
    sorted_ = canonical_sort(stripped)
    # 5. Stamp the root with the draft-07 metaschema URI.
    sorted_["$schema"] = "http://json-schema.org/draft-07/schema#"
    return sorted_
```

The inliner is ~40 LOC (recursive walk + cycle-detection set + deep-copy); the validator is ~30 LOC (closed-keyword set membership check at every node); the canonicalizer is `json.dumps(d, sort_keys=True, separators=(",", ":"))` followed by `json.loads`. The generator is approximately 150 LOC including the 6 TypedDict declarations exercised by G13d. If the inliner discovers a `$ref` to a TypedDict class outside the file's local set (e.g. one TypedDict embedding another), the inliner recurses into the embedded TypedDict's `$defs` and inlines the referenced shape there too. Phase 1 implementation MUST land the inliner before any tool can publish a schema; the budget for "thin shim" was ~30 LOC, and the realistic budget per W3 review is ~150 LOC. SDK5's LOC estimate is bumped from 250 → 350 to absorb this; the test count stays at 30 because the inliner is exercised through the same per-tool `inputSchema`/`outputSchema` round-trips.

If pydantic emits a 2020-12-only construct that the profile disallows, the generator raises at build time and the implementer rewrites the TypedDict to fit the profile. The Phase 1 implementation will exercise this on all 6 tool TypedDicts before SDK-FINAL.1.

### 5.2 Error model + ADR-15 (tool-error mapping)

| Error class | Where surfaced | Wire form |
|---|---|---|
| Schema fail (input) | JSON-RPC envelope | `code=-32602 invalid params`, `data: {schema_path, value}` |
| Schema fail (output) | JSON-RPC envelope | `code=-32603 internal error`, `data: {reason: 'internal_output_validation'}` |
| Capability denied | tool result | `isError: true`, `content[0].text="capability denied: <op>"`, `structuredContent: {error_code: 'capability_denied'}` |
| Token expired / revoked | JSON-RPC envelope | `code=-32001 capability/token denied`, reused from Module 7 REPL band |
| Budget exhausted (replay/audit) | tool result | `isError: true`, `structuredContent: {error_code: 'budget_exhausted', retry_after_s}` |
| ~~Cursor stale (`view_at` parent moved)~~ — **reserved for v0.9** | JSON-RPC envelope | `code=-32008 stale cursor` (reused from Module 7 REPL band; v0.8 has no MCP tool that consumes a cursor as input — see ADR-14 — so this row is non-emitting in v0.8 and the code is reserved-only) |

### Resources (server → LLM)

| Resource | URI | Content |
|---|---|---|
| `audit_tail` | `persistence-os://audit/tail` | Live audit-chain projection (last N entries by default). Subscribed via `resources/subscribe`; server-pushed events follow MCP `notifications/resources/updated` semantics. |

### Wire protocol

**v0.8 conformance target:** stdio is the **sole required transport for v0.8 contract conformance**. Streamable HTTP is **EXPERIMENTAL in v0.8** (`--transport http`, gated behind a launch-time `--enable-experimental-http` flag, prints a red banner at startup, and is explicitly NOT covered by the v0.8 stability decorator system) — its auth + Origin-validation + multi-client semantics are pinned at "loopback-only, single-token, single-process, refuse non-localhost Origin" defaults but the surface itself may break in any patch release. The full conformance + auth specification for Streamable HTTP is deferred to **v0.9**, where it lands as a `@stable("v0.9")` adjunct alongside the privacy-arch work. **WebSocket is NOT a standard MCP transport in the targeted spec revision** and is therefore not advertised by the SDK. (The Module 7 REPL `_ws.py` continues to serve the in-house REPL on its own WS port; reusing it for MCP would require either MCP-spec-extension advocacy or a custom non-conformant subset, both of which are explicitly out-of-scope for v0.8.)

**Streamable-HTTP experimental defaults (v0.8 only).** When `--enable-experimental-http --transport http` is set:

- **Bind:** `127.0.0.1:<port>` ONLY. Server refuses to bind to `0.0.0.0` or any non-loopback address (errors at startup with `bind_non_loopback_refused`). Operators who want LAN/public exposure must run an external reverse proxy and configure it themselves; the SDK ships no `--bind-public` flag.
- **Origin validation (W3 NIT-4 detail):** Server checks the `Origin` request header on every POST + SSE GET. Three cases, distinct treatment:
  1. **Origin header absent entirely** (e.g. plain CLI / curl that doesn't send Origin) → ALLOWED (legitimate non-browser callers don't send Origin).
  2. **`Origin: null`** (literal four-character string `null`, sent by sandboxed iframes / `data:` URIs / file:// origins) → REJECTED with `403 origin_not_allowed`. The `null` literal is a documented browser-emitted value for opaque/sandboxed origins and is the wedge attackers exploit to bypass naive Origin checks.
  3. **`Origin: <url>`** present → must match the allowlist exactly (default: `http://localhost:<port>` + `http://127.0.0.1:<port>` + values from `--http-allowed-origin`). Allowlist comparison is case-sensitive scheme + host + port; trailing slashes and path components are NOT permitted in allowlist entries.
  
  Mismatches return `403 Forbidden` with body `{"error":"origin_not_allowed","origin":"<value>"}`. This is the standard mitigation for [DNS-rebinding attacks against localhost servers](https://en.wikipedia.org/wiki/DNS_rebinding) and aligns with the MCP spec's HTTP-transport security guidance.
- **Auth:** Every HTTP request must carry `Authorization: Bearer <token>` matching the bootstrapped capability token (per ADR-15a). Missing / wrong / revoked → `401 Unauthorized` with body `{"error":"capability_denied"}`. The token is single-valued per server invocation in v0.8 (one process = one token = one client identity); multi-token / multi-client is a v0.9 surface.
- **Sessions (W3 SHOULD-FIX-3 detail):** Each individual HTTP request authenticates independently via its `Authorization: Bearer <token>` header — there is NO server-side session state attached to a TCP connection. The optional SSE stream (`GET /sse`) is also authenticated by the `Authorization` header on the GET request itself; the server holds the SSE response open and pushes `notifications/resources/updated` events on the same response. If the underlying TCP connection drops mid-stream, the client reconnects with a fresh `GET /sse` carrying the same `Authorization` header — there is no session-id, no resume cookie, no replay-from-last-event. This is the simplest correct interpretation of "Streamable HTTP" for v0.8: stateless POST request/response + a long-lived SSE GET, both authenticated per-request. v0.9 may introduce session-id semantics if the privacy-arch work demands it.
- **Rate-limit:** Per-token rate limits inherit from ADR-13 (replay) and the existing Module 7 token-rate-limit handler. Cross-tool global rate is bounded at 60 req/sec per token (configurable via `--http-rate-limit-rps`).
- **TLS:** Plaintext only in v0.8 (loopback assumption). Production deployments wrap behind a reverse proxy (nginx / Caddy / Traefik) that handles TLS — same posture as Module 7 REPL.

These defaults are NOT promised stable across patch releases (the `--enable-experimental-http` gate is the contract). Stdio remains the only `@stable("v0.8")` transport. Tokens issued via `python -m persistence.sdk.mcp --mint-token --capabilities mcp.remember,mcp.recall,mcp.forget,mcp.audit-read,mcp.replay,mcp.view --label <label>` follow the existing Module 7 token ceremony — see § 5.3 below for the cross-process token-handoff path that subsumes the stdio-subprocess case.

### 5.3 Token bootstrap across stdio-subprocess (cross-process semantics)

When an MCP host (Claude Desktop, Cursor, Cline) launches the server as a stdio subprocess, the server must read its capability token from a non-stdin source (stdin is the JSON-RPC channel). v0.8 supports two bootstrap paths, in priority order:

1. **`--token-file <path>`** (recommended for production) — the host writes a single-line file with the token before launching the server. The server reads it once at startup and `os.unlink`s the file (defense-in-depth). The host wires this in its config (Claude Desktop `mcpServers.<name>.env` or `args`).
2. **`PERSISTENCE_MCP_TOKEN=<value>`** (env-var fallback) — the host launches the server with the env-var set. Token never appears in argv.

The server NEVER accepts the token over stdin. The `--token TOKEN` argv form is reserved for development only (logged with a deprecation warning) because process-list inspection (`ps`) leaks it. The token must reference a token-id that already exists in the fact store backing the server's `--store` URI; minting happens out-of-band via `--mint-token` against the same store. (For an in-memory `--store memory` server, the host must mint into the SAME process's store, so `--mint-token` and `serve` happen in one invocation: `python -m persistence.sdk.mcp --store memory --mint-token --label desktop --serve`.) ADR-15a § 6 captures the rationale.

### Why MCP first (not REST / not gRPC / not LangChain)

- **MCP is the de-facto agent-tool protocol.** Anthropic specced it; OpenAI / Cursor / Cline / Claude Desktop / many LLM clients support it. Targeting it first reaches the most agents per LOC.
- **Stdio transport is zero-config** for desktop agents.
- **Schema is small.** 6 tools + 1 resource fits in <300 LOC.
- **Versioning is built in.** MCP includes a protocol-version field at handshake; we declare `2025-06-18` as the spec revision and `persistence-os/v0.8` as our SDK contract version (separate axis).

---

## 6. ADRs

### ADR-1: `Substrate` is a curated namespace, not a wrapper — module attributes are escape hatches, NOT contract surface

**Decision.** The `Substrate` class exposes the existing Module surfaces via attribute access (`s.fact`, `s.effect`, etc.). It does NOT wrap, proxy, or re-implement any Module method. Adapter authors who need fine control reach through to the underlying Module (`s.fact.transact(...)`).

**Contract-boundary clarification (W1 + W2 closure of R1 BLOCKER 3 + R2 SHOULD-FIX 3).** The `s.fact` / `s.effect` / `s.plan` / `s.replay` / `s.txn` / `s.spec` / `s.repl` attributes are **escape hatches, not contract**. The v0.8 adapter contract covers ONLY the symbols re-exported under `persistence.sdk.*` and explicitly decorated `@stable("v0.8")` (per ADR-5 + the spec-doc generator at G7). Adapter authors are documented to:

- pin their imports to `from persistence.sdk import Substrate` + the curated `persistence.sdk.<sub>` re-exports;
- treat any reach-through (`s.fact.<anything>`) as **out-of-contract** — its shape may change at any release, including patch bumps;
- the v0.8 contract spec doc has a top-level box stating "The reachable surface from a `Substrate` instance is larger than the contract; only `persistence.sdk.*` symbols decorated `@stable("v0.8")` are covered. Module attributes (`s.fact` etc.) are escape hatches and may break at any release";
- the `Substrate.fact` / etc. attribute getters carry a docstring + a `@experimental` marker (NOT `@stable`) so the decorator-driven spec generator (G7) does NOT advertise them in the contract.

**Escape-hatch first-access telemetry (W2 SHOULD-FIX 3 closure; W3 NIT-5 contract-shape pin).** The first time per-session that an adapter accesses any of the 7 escape-hatch attributes (`s.fact`, `s.effect`, `s.plan`, `s.replay`, `s.txn`, `s.spec`, `s.repl`), the Substrate emits exactly one `:sdk/escape-hatch-access` audit entry recording `{module: "fact" | ...}`, `caller_filename` (best-effort via `sys._getframe`), `session_id`. Subsequent accesses to the same attribute in the same session are silent (no spam). This is a measurement signal — adapter authors who never reach through have a clean audit trail; adapter authors who do reach through generate a record we can use during Phase 2 dogfooding to identify which Module methods need to be folded into a curated `persistence.sdk.<sub>` namespace at v0.9. Adapter authors are NOT required to suppress these entries — they are diagnostic provenance, not warnings or errors. The session-id-keyed deduplication is server-state only; cross-session reach-through emits one entry per session per attribute, which is the right signal granularity for a "is this load-bearing in the wild" observation.

**The audit-entry shape is `@experimental` (W3 NIT-5 closure), NOT `@stable("v0.8")`.** Specifically, the `:sdk/escape-hatch-access` AuditEntry's `args` payload shape (`{module, caller_filename, session_id}`) is decorated `@experimental` in `_stability.py` and is therefore EXCLUDED from the spec generator's contract output (G7). The shape MAY change in any patch release; downstream tooling that parses these entries does so at its own risk. This protects the diagnostic-telemetry use case (we want to evolve the shape during Phase-2 dogfooding) while preventing it from accidentally becoming a relied-upon contract surface. A docstring on the audit-entry-emitting helper makes this explicit.

A second, opt-in alternative (`s.escape.fact()` style method-call form) was considered and rejected for v0.8: it would force a one-line refactor on every existing internal call-site (`db = s.fact` style code already lives in v0.7.0a1 examples), and the audit-telemetry path provides the same signal without the ergonomic break. v0.9 may revisit if telemetry data argues for the harder boundary.

**Why option-2 (escape hatches NOT contract) over option-1 (wrap-everything-in-curated-namespaces) for v0.8.** Option-1 would force us to design and freeze a curated namespace API for every `DB`/`Runtime`/etc. method an adapter might want, in 5 days, before the coding-agent build (Phase 2) tells us which Module methods adapters actually reach for. That is the worst possible time to commit. Option-2 keeps escape-hatch ergonomics for Phase-2 dogfooding and uses the spec doc + decorator metadata to make the contract boundary explicit and machine-checkable. v0.9 (post Phase 2) is the right time to fold any reach-through pattern that proves load-bearing into a curated stable namespace.

**Rationale.** Wrapping creates two parallel APIs that drift over time (Module-direct vs Substrate-mediated). A facade that adds `__enter__/__exit__` + a curated attribute namespace + 3 lifecycle helpers is enough to give adapters a stable opening hook without sacrificing direct-Module ergonomics. Three real wrappers we considered and rejected: a `transact()` method on Substrate (would compete with `s.fact.transact`), a `remember()` method on Substrate (would compete with the MCP layer's `remember` tool), an `audit()` method on Substrate (would compete with the existing audit-handler factory).

**Consequence.** Stability promises live in `_stability.py`'s decorators applied to the Module-level symbols re-exported under `persistence.sdk.*`, not on the Substrate class itself and not on raw module attributes. The Substrate's job is namespace + lifecycle, full stop. Adapter contract boundary is enforced by (a) decorator metadata, (b) the spec generator, (c) the README + spec-doc warning box, NOT by Python access-control.

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

### ADR-13: `persistence_replay_check` ships a safety budget; output is a verdict + reason code, not a diff

**Decision.** The MCP `persistence_replay_check(tx, window?=32)` tool runs the replay engine over a bounded `(tx − window/2 … tx + window/2)` window and returns ONLY `{ok: bool, reason_code: str, window_actual: int, head_hash: str}`. It does NOT return the byte-diff, the user-write log, or the audit-chain projection. Reason codes ∈ `{ok, mismatch_user_log, mismatch_audit_chain, window_too_large, replay_aborted_budget, tx_not_found}`. The structured output is `@stable("v0.8")`.

**Budget pin.** Public defaults (overridable via server-side config, never via untrusted client input):

- `MCP_REPLAY_MAX_WINDOW = 256` entries — `window > MCP_REPLAY_MAX_WINDOW` returns `isError: true, reason_code='window_too_large'`.
- `MCP_REPLAY_MAX_WALLCLOCK_S = 5.0` — server aborts mid-replay with `reason_code='replay_aborted_budget'`.
- `MCP_REPLAY_RATE_LIMIT_PER_TOKEN = 6 / minute` — server returns `isError: true, structuredContent: {error_code: 'budget_exhausted', retry_after_s}`.
- These values land in `persistence.sdk.mcp._budgets` as module-level constants, audit-emitted at server startup so operators see the active limits.

**Why NOT return the diff.** (a) Diff contents are bitemporal-store contents; an attacker who can call `replay_check` against arbitrary `tx` could exfiltrate the entire substrate state via the diff. (b) A boolean+reason verdict is sufficient for the legitimate use case ("did my recently-remembered thing replay byte-identically? is the chain intact?"); diff exposure is a separate, capability-elevated tool (out of scope for v0.8). (c) Module 7 REPL's `inspect kind=audit-window` is the privileged-operator path for diff-shaped reads; MCP must not duplicate that surface for untrusted callers.

**Operator escape hatch.** A debug-mode flag `--debug-include-diff` MAY be set on the server side at launch time only (NEVER toggleable via the wire) to include `diff_summary` in `structuredContent` for development. The `--version` banner prints a red warning when the flag is set. Production deployments leave it off.

**Consequence.** `replay_check` is safe to expose to any holder of `mcp.replay` capability without a separate "trusted operator" tier. Operators who need diff-shaped reads use `s.repl` (capability-gated, out of MCP).

### ADR-14: `persistence_view_at` is a cursor handle, NOT a store fork — alignment with Module 7 ADR-13

**Decision.** The MCP `persistence_view_at(tx, label?)` tool returns a **cursor handle** `{cursor_id, view_cursor_tx_time_iso, parent_chain_depth, label?}`. It does NOT call any store-fork primitive. The cursor is server-state only — the substrate's underlying store is unchanged after the call. Cursors are scoped to the issuing token and expire when the token expires.

**Naming.** Originally `branch_at` in the v0.8 R0 draft; renamed to `view_at` post-R1 because "branch" in this codebase has a precise prior meaning (Module 7 ADR-13: cursor + depth marker, NOT a store fork). Reusing "branch" for a cursor handle would re-introduce the very ambiguity Module 7 ADR-13 spent its body section banishing. `view_at` mirrors `db.as_of(t)`-style semantics adapter authors already understand.

**Stale-cursor edit rejection.** If a downstream tool later attempts to mutate the substrate against a stale `view_at`-issued cursor (one whose `parent_chain_depth` is below the current head), the server returns `-32008 stale cursor` (reusing the Module 7 REPL error band, ADR-15 § 5.2). v0.8 has no MCP tool that mutates against a cursor — `remember`/`forget` always run against `head` — but the error code is reserved so the cursor + edit story stays internally consistent for v0.9.

**Why expose this in v0.8 at all.** The audit/replay narrative ("agent: 'rewind to 5 minutes ago'") is the demo wedge. A cursor handle that an LLM can produce (`view_at`), then read against (`audit_window` / `replay_check`), is the smallest expressive surface that supports the demo without inventing store-fork semantics. v0.9 may introduce `branch_at_fork(...)` if a real product need surfaces.

**Consequence.** `view_at` returns immediately (no replay; no copy); cost is one cursor record + one audit entry. The cursor record is in-memory per-server (token-scoped); cursor lifetime is bound to token lifetime.

### ADR-15: Tool / audit naming convention — wire vs storage

**Decision.** The MCP server uses three name strings per tool, each with a precise, distinct shape:

| Surface | Form | Has leading `:` | Example |
|---|---|---|---|
| MCP wire (`tools/list` `name`) | `persistence_<verb>` snake_case | no | `persistence_remember` |
| Capability (cap-token op) | `mcp.<verb>` dotted | no | `mcp.remember` |
| `Datom.a` storage | `mcp/op-<verb>` slashed kebab | no | `mcp/op-remember` |
| `AuditEntry.op` wire | `:mcp/op-<verb>` colon-prefixed | **yes** | `:mcp/op-remember` |

**Rationale.** Module 7 REPL set the precedent for the storage-vs-wire colon split (ADR-12 of that doc). The MCP server inherits the same pattern. The new `persistence_<verb>` form is the public-facing tool name (host-aggregator-collision-safe via the `persistence_` prefix per MCP server-tools naming guidance); the internal `mcp.<verb>` capability name stays in the existing Module 7 capability namespace; the `Datom.a` and `AuditEntry.op` forms preserve the stored-vs-wire convention `audit_entry_to_datom` already enforces (`src/persistence/effect/handlers/audit.py:92-96`).

**Consequence.** All 6 tools have a fixed, enumerated row in this table:

| Verb | MCP wire | Capability | Datom.a | AuditEntry.op |
|---|---|---|---|---|
| remember | `persistence_remember` | `mcp.remember` | `mcp/op-remember` | `:mcp/op-remember` |
| recall | `persistence_recall` | `mcp.recall` | `mcp/op-recall` | `:mcp/op-recall` |
| forget | `persistence_forget` | `mcp.forget` | `mcp/op-forget` | `:mcp/op-forget` |
| audit_window | `persistence_audit_window` | `mcp.audit-read` | `mcp/op-audit-window` | `:mcp/op-audit-window` |
| replay_check | `persistence_replay_check` | `mcp.replay` | `mcp/op-replay` | `:mcp/op-replay` |
| view_at | `persistence_view_at` | `mcp.view` | `mcp/op-view` | `:mcp/op-view` |

The decorator-driven spec generator (G7) reads this table from a single `_NAMES` dict in `persistence.sdk.mcp._tools` and emits all four forms — drift is impossible by construction.

### ADR-15a: Cross-process token bootstrap for stdio-launched MCP servers

**Decision.** When an MCP host launches the server as a stdio subprocess, the server reads its capability token from a file (`--token-file`) or environment variable (`PERSISTENCE_MCP_TOKEN`), NEVER from stdin (which is the JSON-RPC channel) and NEVER from argv (visible to `ps`). The `--token` argv form is dev-only and emits a deprecation warning.

**Rationale.** A stdio-launched MCP server cannot share an in-process token store with its host; the token must be issued out-of-band against the SAME `--store` URI that the server will open. Three plausible bootstrap paths exist:

1. Token file path — host writes a one-line file pre-launch, server reads + unlinks.
2. Env-var — host launches with `PERSISTENCE_MCP_TOKEN=<value>` set; the value is whatever the host already has from a prior `--mint-token` invocation.
3. argv — visible to `ps`, leaks. Dev-only.

The fact-store-backed token registry (Module 7 ADR-3) is the source of truth; bootstrap just delivers an opaque pointer to a record that already exists. For an in-memory `--store memory` server (the common case for desktop hosts that don't want to wire up sqlite), the host launches with `--mint-token --serve` in a single invocation so minting and serving share the in-process store.

**Consequence.** v0.8 ships three bootstrap modes (file, env, argv-dev-only). G2 covers all three. Token leakage via `ps` is impossible in production paths. R4 (risk table) gains a new row for cross-process token-handoff to make this explicit.

### ADR-15b: Streamable HTTP is EXPERIMENTAL in v0.8 — explicit non-conformance

**Decision.** The `--transport http` Streamable-HTTP path is **experimental in v0.8** and is NOT part of the v0.8 stability contract. It is gated behind `--enable-experimental-http` (refuses to start without it), prints a red `EXPERIMENTAL` banner at startup, and is excluded from the `@stable("v0.8")` decorator audit set. Any aspect of the HTTP path may break in any patch release. Stdio is the sole `@stable("v0.8")` transport.

**Why experimental, not full conformance, in v0.8.** Adding a network-facing HTTP transport to a 5-day window means specifying (at minimum): (a) localhost-bind defaults + the refusal to bind elsewhere, (b) Origin validation against DNS-rebinding, (c) Authorization header semantics + token-to-session mapping, (d) per-connection rate limiting, (e) interaction with the cross-process token bootstrap path. Each item is a security surface that a hostile reviewer would (rightly) probe. v0.9's privacy-arch work is the natural venue to fold these in alongside the confidentiality story (ADR-17). v0.8 ships the experimental path with conservative locked defaults so internal testing and Phase-2 dogfooding can validate the shape, but does NOT ship a stability promise.

**Pinned defaults in v0.8 (§ 5 wire-protocol block).** Loopback-only bind, Origin allowlist with localhost defaults, single-token-per-server, Bearer-token Authorization, plaintext (TLS via reverse proxy), per-token rate limit. These defaults ARE asserted by gate G12 — the EXPERIMENTAL marker means consumers shouldn't pin their integration to them, NOT that the defaults are unenforced. The implementation enforces them; the contract just doesn't promise to keep them stable.

**Path to stability.** v0.9 design-doc (Phase 3 privacy-arch) re-evaluates the HTTP path against: (a) multi-tenant deployments, (b) per-token capability sets across connections, (c) TLS termination, (d) OAuth 2.0 / SSO bootstrap as alternative to `--token-file`, (e) audit-emission semantics for HTTP-side metadata (Origin, IP, User-Agent). The v0.9 path may either freeze the v0.8 defaults as `@stable("v0.9")` or supersede them.

**Consequence.** The MCP server's `--version` banner shows `transport: stdio (stable) | http (EXPERIMENTAL — not covered by v0.8 contract)`. The contract spec doc explicitly states the same.

### ADR-16: `@stable("v0.8")` semantics — what counts as a breaking change

**Decision.** A symbol decorated `@stable("v0.8")` carries the following machine-checkable promises across all `0.8.x` releases. Breaking ANY of these is a contract violation that requires a minor-version bump (`0.9.0`):

| Aspect | Stability promise |
|---|---|
| **Symbol identity** | The symbol is reachable at the same import path; no rename. |
| **Call shape** | Positional + keyword parameters, parameter names, default values — all preserved. New keyword params with defaults are additive (allowed). Removing a param or making one stricter (narrower type) is breaking. |
| **Parameter types** | Type annotations may BROADEN (`int → int | float`) but never narrow. |
| **Return type** | Type annotation may broaden but never narrow. |
| **Return-shape (dict / TypedDict)** | Required keys remain present with same value-type. New optional keys are additive (allowed). Removing or renaming a key is breaking. v0.8 ships TypedDict declarations for every dict-returning stable symbol so the shape contract is machine-checkable. |
| **Exception types** | Documented exception classes only. Adding a new documented exception class is additive (allowed). Removing or replacing one with a non-subclass is breaking. |
| **Side effects** | Documented side effects are stable (audit entries emitted, env vars read). Silently adding a new side effect (e.g. logging to a new path) is breaking. |
| **Performance ceilings** | NOT promised at the per-call level for v0.8. Aggregate "no super-linear regression on the canonical benchmark" is a v0.9 conversation. |

**Consequence.** Every stable symbol that returns a dict or `Any` must have a corresponding TypedDict declaration emitted into `persistence.sdk._types` and re-exported under `persistence.sdk.types`. The spec generator (G7) inspects both the decorator metadata AND the TypedDict registry; CI fails the build if a `@stable` symbol returns `dict` without a corresponding TypedDict.

### ADR-17: First-party MCP confidentiality posture in v0.8 — explicit non-goal

**Decision.** v0.8 first-party MCP makes **NO confidentiality guarantees**. The substrate stores `mcp/content` values in plaintext in the underlying fact store; audit entries record the value-hash but not encrypted. No per-tenant isolation, no field-level encryption, no audit-chain hash-only mode. The capability-token system gates ACCESS; it does NOT confidential-isolate stored content.

**Rationale.** Privacy-arch is a Phase 3 line item (per the v1.0 roadmap). Promising any confidentiality property in v0.8 with 5 days of design + impl time would either (a) ship a hand-wavy claim that fails under audit, or (b) bloat the scope. Better to be explicit: v0.8 MCP is for trusted-host scenarios (developer's own laptop running their own substrate). Sensitive-data deployments wait for Phase 3.

**Consequence.** The README, the spec doc, AND the `--version` banner of the MCP server all carry an explicit "no confidentiality guarantees in v0.8" line. Risk table R8 (added below) captures this. v0.9 may introduce per-attribute confidentiality tiers ("public" / "redacted" / "encrypted"); v0.8 is plaintext-only.

---

## 7. Acceptance gates (ARIS R1 + R2)

ARIS R1 (this design doc) target: mean ≥ 8.5 / min ≥ 7.5 per the auto-review-loop hard-mode pattern.

ARIS R2 (post-implementation) target: mean ≥ 8.5 / min ≥ 7.5 on the merged branch tip.

### G1 — Substrate facade load test (smoke)

Open `Substrate.open(store="memory")`, exercise each Module's first-line **already-on-trunk** API:

- `s.fact.transact([Datom(...)])` (existing `DB.transact` at `src/persistence/fact/db.py`)
- `s.txn.dosync(db, lambda tx: tx.assoc(ref, k, v))` (existing `dosync` from `persistence.txn`)
- `s.plan.parse("(plan)")` (existing `parse` from `persistence.plan`)
- `s.replay.replay_session(...)` (existing replay primitive — exact entrypoint pinned by SDK1 at impl time, asserted via `getattr` so a rename in `persistence.replay` is a CI signal, not a silent break)

Close. Assert `s.health_check()` returns green throughout AND `s.health_check()['audit_chain_verified']` is `True`. The gate fails if any reach-through call raises `AttributeError` — that is the early-warning signal that ADR-1's escape-hatch attribute set drifted out of sync with the underlying modules.

### G2 — MCP server end-to-end (stdio + lifecycle conformance)

Start `python -m persistence.sdk.mcp --store sqlite:///$TMP/g2.db --transport stdio --token-file $TMP/g2.tok` in a subprocess (the test fixture mints the token into the same sqlite store before launching). Connect via stdio JSON-RPC. Assert the FULL MCP lifecycle in order:

1. Client sends `initialize` with `protocolVersion: "2025-06-18"`, `capabilities: {tools: {}, resources: {subscribe: true}}`, `clientInfo: {name:"g2-test", version:"0.1"}`. Server replies with matching `protocolVersion` (or `2025-03-26` fallback per spec) and its own `capabilities` + `serverInfo`.
2. Client sends `notifications/initialized`. Server enters ready state.
3. Client sends `tools/list`. Server reply enumerates exactly 6 tool entries with the names from ADR-15's table; each entry has a non-empty `inputSchema` and (where applicable) `outputSchema`.
4. Client sends `tools/call` with `name=persistence_remember`, `arguments={content:"hello world", tags:["greeting"]}`. Result has `isError: false`, `structuredContent.eid` is a UUID, `content[0].type=="text"` for the fallback string.
5. Client sends `tools/call` with `name=persistence_recall`, `arguments={query:"hello", k:5}`. Result `structuredContent.hits[0].eid` matches the eid from step 4.
6. Client sends `resources/list`. Server reply includes the `audit_tail` resource at `persistence-os://audit/tail`.
7. Client sends `resources/subscribe` for the `audit_tail` URI. Within 2 seconds the test triggers another `tools/call persistence_remember`; the client receives an MCP `notifications/resources/updated` for `audit_tail` referencing the new tx.
8. Inspect the underlying `--store` (sqlite) directly: assert exactly 3 entries with `Datom.a in {"mcp/op-remember", "mcp/op-recall", "mcp/op-remember"}` (one per tool call), all `prev_hash`-chained correctly via `verify_chain`.

The test runs against BOTH `sqlite:` and `memory:` stores (parametrized), but **stdio transport ONLY** — Streamable HTTP is EXPERIMENTAL (per ADR-15b) and exercised separately in G12 (HTTP-experimental regression). G2 is the v0.8 *conformance* gate; mixing HTTP into it would conflate "stable contract you can pin against" with "experimental surface that may break in patch". (W3 SHOULD-FIX-2 closure: explicit conformance/experimental separation.)

### G3 — Stability decorator system

`SDK.list_stable()` returns the curated set of `@stable("v0.8")` symbols. Calling a `@deprecated` symbol emits both a Python warning AND a `:sdk/deprecated-call` audit entry. Calling an `@experimental` symbol emits no warning. Calling a `@stable("v0.8")` symbol emits nothing.

### G4 — Audit chain integrity across SDK + MCP traffic

Mix substrate-direct traffic (`s.fact.transact(...)`), Module 7 REPL traffic (`s.repl.serve` + an op), and MCP traffic (`mcp.remember`). Run `verify_chain()` over the entire window. Must return `True` on a clean run. Tampering with one entry must surface as `False`.

### G5 — License banner on every public artifact

`pyproject.toml` license = AGPL-3.0-or-later. Spec doc has AGPL banner. README has AGPL banner. MCP server `--version` output has AGPL banner. Test asserts these are in sync.

### G6 — Backend URI parsing

`Substrate.open(store="memory")` / `sqlite:///tmp/x.db` / `postgres://...` all dispatch to the right backend. `postgres://...` in the absence of the `[postgres]` extra raises `BackendNotInstalled`. Future backends register via a plugin point.

### G7 — Spec doc generated from decorators

Run a doc-generation script (`scripts/gen_adapter_contract.py`) that reads decorator metadata from the SDK and emits `docs/spec/adapter-contract-v0.8.md`. Assert the committed spec doc and the generated output match (CI gate). Generator pins:

- import-order is deterministic (alphabetical by module then symbol);
- TypedDict registry from `persistence.sdk.types` is included for every `@stable` dict-returning symbol; CI fails if a `@stable` symbol returns `dict | Any` without a TypedDict (per ADR-16);
- ADR-15's tool-name table is read from the `_NAMES` dict in `persistence.sdk.mcp._tools` (single source of truth — drift impossible by construction);
- output is byte-identical across Python 3.11+ on macOS and Linux (no platform-dependent ordering / timestamps in the output).

### G8 — `persistence_replay_check` safety budget enforced

Three sub-gates, all asserted against the running MCP server from G2:

- **G8a:** `tools/call persistence_replay_check {tx: <valid>, window: 1024}` returns `isError: true, structuredContent.error_code='budget_exhausted'` (or `reason_code='window_too_large'`) — no replay runs.
- **G8b:** Synthetic-slow handler test: a fact-store fixture inflates replay wall-clock above `MCP_REPLAY_MAX_WALLCLOCK_S` for one `tx`. The call returns `isError: true, structuredContent.error_code='budget_exhausted', reason_code='replay_aborted_budget'`; `verify_chain` over the audit window emits a `:mcp/op-replay` entry with `result_hash` derived from the budget-exhausted shape, NOT from a partial diff (audit emission still happens).
- **G8c:** 7 successive successful `replay_check` calls within 60 seconds: 6 succeed, the 7th returns `isError: true, error_code='budget_exhausted', retry_after_s>0` (per `MCP_REPLAY_RATE_LIMIT_PER_TOKEN=6/min`). Test fast-forwards mock clock by 60s, asserts the 8th call succeeds.
- **G8d:** Output of every `replay_check` call has NO key named `diff` / `byte_diff` / `user_log` in `structuredContent`. The test reflectively walks the dict and asserts the closed key set `{ok, reason_code, window_actual, head_hash}`.

### G9 — `persistence_view_at` is a cursor handle, NOT a store fork

- **G9a:** `tools/call persistence_view_at {tx: 5, label: "before-feature-X"}` returns `structuredContent.cursor_id` (UUID), `parent_chain_depth >= 1`, `view_cursor_tx_time_iso` set. Inspect `s.fact._store` directly: assert no new branch, no new store fork, datom count unchanged from pre-call.
- **G9b:** Two `view_at` calls against the same `tx` return TWO different `cursor_id`s but identical `view_cursor_tx_time_iso` and `parent_chain_depth` (cursors are per-call, not per-tx).
- **G9c:** A cursor outlives its issuing token only by token-lifetime; revoking the token invalidates the cursor. Test revokes mid-session, asserts subsequent reference to the cursor in any future tool call (v0.9 onwards) WOULD return `-32008 stale cursor`. v0.8 has no MCP tool that consumes a cursor as input, so this gate is currently a token-revocation smoke + a documentation pin; it tightens at v0.9.

### G10 — `@stable("v0.8")` semantics — TypedDict + signature lockfile

- **G10a:** Every `@stable("v0.8")` symbol that returns a dict has a corresponding TypedDict in `persistence.sdk.types`; CI gate generated by `scripts/gen_adapter_contract.py` fails the build otherwise.
- **G10b:** The spec generator emits a machine-readable `adapter-contract-v0.8.lock.json` alongside the markdown spec; the lockfile records `{symbol, signature_sha, return_type_sha, exception_classes}` for every `@stable("v0.8")` symbol. CI compares the committed lockfile against a fresh generation; any diff fails the build (forces an explicit "yes I am breaking the contract" gesture).
- **G10c:** A unit test calls each `@stable` symbol with a representative arg shape and asserts the returned dict's keys match the TypedDict's required keys exactly. (This catches the "documented TypedDict but actual return doesn't conform" failure mode.)

### G11 — MCP confidentiality non-goal posted in artifacts (ADR-17)

- README, spec doc, AND `python -m persistence.sdk.mcp --version` output all emit the line "v0.8 MCP makes NO confidentiality guarantees — for trusted-host scenarios only; sensitive-data deployments wait for v0.9 privacy-arch."
- A unit test grep-asserts the line is present in all three.

### G12 — Streamable-HTTP experimental defaults regression test (W2 BLOCKER 1)

The `--enable-experimental-http --transport http` server is exercised in a separate test module (NOT in the conformance suite — the gate emits `experimental` markers everywhere). Tests assert:

- **G12a (bind):** `--bind 0.0.0.0` exits with `bind_non_loopback_refused` at startup. `--bind 127.0.0.1` succeeds.
- **G12b (Origin):** A POST with `Origin: http://evil.example` returns `403 origin_not_allowed`. A POST with `Origin: http://localhost:<port>` succeeds. A POST with no Origin header succeeds. The `--http-allowed-origin http://my.dev` flag adds that origin to the allowlist.
- **G12c (Auth):** A POST with no `Authorization` header returns `401 capability_denied`. A POST with `Authorization: Bearer <wrong-token>` returns `401`. A POST with the correct token succeeds. A POST after `revoke_token` of the in-use token returns `401`.
- **G12d (Banner + experimental marker):** Server startup with `--enable-experimental-http` prints a red-coded banner to stderr containing `EXPERIMENTAL` and a link to the v0.9 stability promise. A unit test grep-asserts the banner text.
- **G12e (Rate-limit):** 61 successive successful POSTs in <1 second; 60 succeed, the 61st returns `429 rate_limit_exceeded` with `Retry-After`.

These gates are NOT part of the v0.8 contract conformance suite (HTTP is experimental); they are part of the v0.8 *implementation* test suite to ensure that the experimental defaults the doc commits to are actually enforced. v0.9 will fold the HTTP path into the conformance suite when the surface stabilizes.

### G13 — Schema Profile v0.8 enforcement (W2 BLOCKER 2)

- **G13a:** The generator (`scripts/gen_adapter_contract.py`) on every TypedDict produces a JSON-Schema document with `$schema: "http://json-schema.org/draft-07/schema#"` and zero disallowed keywords (per § 5.1.1 closed list). Disallowed keyword usage at any nesting depth raises `SchemaProfileViolation`.
- **G13b:** Every emitted schema is validated by `jsonschema-validator>=4` against draft-07 metaschema. Profile-conformant + metaschema-valid both required.
- **G13c:** Every tool's `inputSchema` AND `outputSchema` SHA-256 is recorded in `adapter-contract-v0.8.lock.json` `schemas` block. CI gate fails on any SHA diff.
- **G13d:** The 6 tools' canonical TypedDicts in `_types.py` are exercised by a smoke test that calls `pydantic.TypeAdapter(TD).validate_python({...})` against representative payloads (one valid + one invalid per tool); validates the generator-emitted JSON-Schema accepts/rejects matching values.

---

## 8. Task breakdown (subagent dispatch model)

Per the v0.5-txn Phase B / v0.6.0a1 / v0.6.5 / v0.7.0a1 precedent: per-task subagent dispatch with two-stage review (implementer + spec-reviewer + code-quality-reviewer). 5 tasks + integration + ARIS gates.

| Task | Scope | Estimated LOC + tests |
|---|---|---|
| **SDK1** — `_facade.py` + `Substrate` class + `_stability.py` decorators | core SDK + decorator system + ~20 unit tests | ~250 / ~80 |
| **SDK2** — `_health.py` + `health_check`, `version_info`, `module_status` + ~10 tests | health probes | ~120 / ~40 |
| **SDK3** — `mcp/_server.py` + `mcp/_tools.py` + 6 tools + JSON-RPC dispatch + ~30 tests | MCP server core | ~250 / ~120 |
| **SDK4** — `mcp/__main__.py` runner + token mint CLI + AGPL banner + ~15 tests | runnable artifact | ~80 / ~40 |
| **SDK5** — `docs/spec/adapter-contract-v0.8.md` + `scripts/gen_adapter_contract.py` (with `$ref` inliner, profile validator, key canonicalizer per § 5.1.1) + CI gate | spec doc + schema generator | ~350 / ~30 |
| **SDK-INT** — end-to-end integration test: open Substrate → mint MCP token → boot MCP server → remember/recall/forget → verify_chain across SDK + REPL + MCP traffic | e2e | ~150 |
| **SDK-FINAL.1** — ARIS R2 code-quality (codex hard-mode) | review | — |
| **SDK-FINAL.2** — CHANGELOG + version bump `0.7.0a1` → `0.8.0a1` + local tag | release | — |

Total estimated impl: ~1200 LOC src + ~310 tests. 5 days. (Bumped from 1100 → 1200 in W3 to absorb the schema-generator $ref-inliner per W3 SHOULD-FIX-1.)

---

## 9. Open questions (defer to ARIS R1 review or impl phase)

R1 round-1 surfaced that the original 5 OQs were not the actual blockers. The list below is rewritten to lead with the structural questions and demote the original five to OQ-6 through OQ-10.

1. **(BLOCKER-class) Stable surface boundary enforcement.** ADR-1 (W1-revised) states the `s.fact` etc. attributes are escape hatches and not contract; ADR-5 + G7 lock the contract surface to `@stable("v0.8")` decorator metadata. Question: is decorator metadata + spec-doc warning + `@experimental` marker on the escape-hatch attribute getters enough, or do we need a runtime check (e.g. `Substrate.fact` returns a wrapper that emits an `:sdk/escape-hatch-call` audit warning the first time it's accessed per-session)? **Recommend:** decorator metadata only for v0.8; revisit if `persistence-coder` proves to reach through frequently.
2. **(BLOCKER-class) MCP spec revision drift.** v0.8 pins `2025-06-18`. The spec is on a quarterly rev cycle; v0.8.x patch releases will need to track. Question: how do we keep the SDK current without breaking the contract? **Recommend:** the negotiated `protocolVersion` is server-side state, NOT part of the `@stable` SDK surface. v0.8.x patch releases may add support for newer revs (additive); they may NOT drop `2025-06-18` support (breaking). v0.9 may freeze a higher revision as the new minimum.
3. **(BLOCKER-class) Tool schema authoring discipline.** ADR-15 fixes the names; § 5 sketches the input/output shapes. Question: do we hand-author each `inputSchema` / `outputSchema` JSON or generate from Python TypedDicts? **Recommend:** generate from TypedDicts via a thin `pydantic.TypeAdapter`-style helper at server-init time; hand-authoring drifts. v0.8 ships the generator; v0.9 may switch to pydantic-v2 if its JSON-Schema output stabilizes.
4. **(BLOCKER-class) `view_at` cursor lifetime + sweep.** ADR-14 says cursors are token-scoped and outlive the call. Question: do we need a server-side sweep when a token is revoked, or does next-use rejection (`-32008 stale cursor`) suffice? **Recommend:** next-use rejection only for v0.8 (cursors are tiny, no leak risk). v0.9 may add a sweep when the cursor table grows.
5. **(BLOCKER-class) `replay_check` budget defaults.** ADR-13 picks `window=256`, `wallclock=5s`, `rate=6/min`. Question: are these calibrated against real substrates, or are they finger-in-air? **Recommend:** finger-in-air today; the SDK-INT test suite measures actual replay wall-clock at ~5 datoms/ms on the v0.6.5 fact-store, so 256-entry budget = ~50ms (well under 5s). Calibrate against `persistence-coder` workload during Phase 2; if 256 is too restrictive, raise in v0.8.x as an additive default change (downward-compatible — old clients pass `window<=256`, new clients pass `window<=512`, server defaults to 256 if unspecified).

6. **Should `Substrate` expose a `context` mechanism for per-call request-id / tenant-id propagation?** Alternative: adapter authors handle it via ContextVar at the call site. Recommend: ContextVar at call site for v0.8; revisit if `persistence-coder` needs per-request tagging.
7. **MCP `recall` ranking — recency only, or also a small tag-relevance score?** v0.8 default: recency-only, k=5. Tag relevance is a `recall_with_score(query, tags, k)` separate symbol if needed.
8. **Should the MCP server expose a `subscribe(audit-tail)` resource alongside the 6 tools?** v0.8 default: YES, via MCP `resources/subscribe` + server-pushed `notifications/resources/updated` (transport-conformant: stdio uses MCP notification frames, Streamable HTTP uses SSE). Audit traffic is the entire moat. (W1 update: WS transport dropped per ADR-15's spec-conformance pin.)
9. **Capability tokens for SDK consumers** — should an in-process `Substrate.open()` consumer also need a token, or is process-local trust sufficient? Recommend: token NOT required for in-process; required for any out-of-process or network-served consumer (including MCP via stdio subprocess — see ADR-15a).
10. **Health-check audit-chain probe window size** — default 10 entries vs default 100 entries vs explicit user-set? Recommend: configurable via `health_check(window=N)`; default 10.

---

## 10. Risks + mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Adapter contract decisions ossify before we know what `persistence-coder` needs | medium | high | ADR-7 — patch versions preserve contract; minor versions allow breaks. Phase 2 dogfooding has a defined "contract amendment" surface in `0.9.0`. |
| R2 | MCP protocol evolves under us mid-Phase 2 | low | medium | Version handshake (ADR-15 pins `2025-06-18` with `2025-03-26` fallback); spec doc explicitly notes MCP version targeted; OQ-2 documents the patch-vs-minor policy for tracking newer revs. |
| R3 | AGPL license discourages adoption | medium | medium | Documented in Chris-brief as a deliberate moat. Commercial dual-license offered post-incorporation. |
| R4 | `Substrate` becomes a god-object over time | medium | medium | ADR-1 hard-pin: facade is namespace + lifecycle, no wrapping. Code-review gate enforces. |
| R5 | Stability decorator drift between code and spec doc | low | high | ADR-5 + ADR-16 + G7 + G10 — spec doc + lockfile generated from decorators + TypedDicts; CI gate fails if drift detected. |
| R6 | Audit chain explosion from chatty MCP traffic | medium | low | Per-tool capability limits via Module 7 tokens; per-tool rate limits in ADR-13 (`replay`); rate-limit handler stack already exists. |
| R7 | Token leakage via verbose logs | medium | high | Token IDs (sha256[:16]) logged; raw tokens NEVER. Inherits Module 7 token discipline. |
| R8 | **(R1 W1 add)** Stable-surface contract leaked via raw module attributes (`s.fact`/`s.effect`/etc.) | high | high | ADR-1 W1-revised + ADR-16 — escape-hatch attributes carry `@experimental` marker, NOT `@stable`; spec generator excludes them; spec doc + README + `--version` banner all carry the "escape hatches not covered" line. Decorator-driven contract enforces machine-checkability. |
| R9 | **(R1 W1 add)** MCP tool name collision when host aggregates from multiple servers | medium | medium | ADR-15 — every tool name prefixed `persistence_`; spec doc lists the prefix as load-bearing; G2 asserts the prefix is present on every advertised tool. |
| R10 | **(R1 W1 add)** `replay_check` resource burn / state exfil via diff returns | medium | high | ADR-13 — fixed budgets (window 256, wallclock 5s, rate 6/min/token), result is verdict+reason-code only, NEVER raw diff. G8 asserts all four budget paths and the closed-key-set output shape. |
| R11 | **(R1 W1 add)** Cross-process token bootstrap from MCP host leaks token via argv (`ps`) | medium | high | ADR-15a — `--token-file` (read+unlink) and `PERSISTENCE_MCP_TOKEN` env-var are the supported paths; `--token` argv form deprecation-warns. G2 fixture uses `--token-file` exclusively. |
| R12 | **(R1 W1 add)** First-party MCP exposes plaintext content with no privacy guarantees → users assume confidentiality where none exists | medium | high | ADR-17 — explicit non-goal in v0.8; banner / README / spec-doc / `--version` all carry the "no confidentiality guarantees in v0.8" line. G11 asserts the line appears in all three artifacts. v0.9 privacy-arch is the proper venue. |
| R13 | **(R1 W2 add)** Local-web attack surface from experimental Streamable-HTTP transport (DNS-rebinding, cross-origin token theft, multi-client confusion) | medium | high | ADR-15b — HTTP path is EXPERIMENTAL in v0.8 (not @stable, gated behind `--enable-experimental-http`, prints `EXPERIMENTAL` banner). Pinned defaults: loopback-only bind (refuses non-loopback), Origin allowlist with localhost defaults, Bearer-token Authorization, single-token-per-server-process, per-token rate limit. G12a-e asserts each default is enforced. Stability promise deferred to v0.9 alongside privacy-arch. |
| R14 | **(R1 W2 add)** Schema generator drift / cross-client incompatibility from MCP clients with uneven JSON-Schema 2020-12 support | medium | high | ADR-15 § 5.1.1 (Schema Profile v0.8) pins draft-07 with conservative-keyword subset; generator rejects disallowed keywords; per-tool schema SHA recorded in `adapter-contract-v0.8.lock.json`. G13a-d asserts profile + lockfile + metaschema validity + round-trip pydantic↔JSON-Schema. |

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
5. On SDK-FINAL.2 PASS: tag `v0.8.0a1`, append CHANGELOG, persist Serena + vault + auto-memory, conductor STATUS Phase 1 block close.

---

## ARIS R1 status — PASS at mean 8.56 / min 8.20 (W3)

**Auto-review-loop hard mode**, codex CLI gpt-5.2 high reasoning, MAX_ROUNDS=4, autonomous (no human checkpoint), 2026-04-29.

| Round | Mean | Min | Verdict | Δ | W-cycle commits |
|---|---|---|---|---|---|
| R1 | 6.40 | 5.50 | NOT READY | — | (3 BLOCKERs, 3 SHOULD-FIX, 1 NIT) |
| R2 | 7.99 | 7.60 | NOT READY | +1.59 / +2.10 | W1 = `1a312f5` (7 fixes — MCP transport / lifecycle / tool schemas / Substrate escape-hatch boundary / view_at cursor / replay_check budget / naming convention) |
| R3 | **8.56** | **8.20** | **READY** | +0.57 / +0.60 | W2 = `60380e2` (5 fixes — HTTP experimental gate / Schema Profile v0.8 / escape-hatch first-access telemetry / doc consistency / opaque cursor format) |

**R3 W3 polish** applied in the present commit: schema-generator `$ref` inliner explicit, G2 stdio-only conformance vs G12 HTTP experimental separation, HTTP per-request auth (no per-connection session) clarification, `Origin: null` literal vs missing-Origin distinction, escape-hatch audit-entry shape pinned `@experimental`. SDK5 + total LOC bumped 250→350 / 1100→1200.

**Bar:** mean ≥ 8.5 / min ≥ 7.5 — **PASSED** at R3.

**Closed across the loop:**
- 3 R1 BLOCKERs (MCP transport+lifecycle, tool schemas + result shapes, Substrate stable-surface leak)
- 3 R1 SHOULD-FIX (`branch_at`→`view_at` cursor, replay safety budget, naming convention)
- 1 R1 NIT (LOC reconciliation + `v0.8.0`→`v0.8.0a1`)
- 2 R2 BLOCKERs (Streamable-HTTP under-specified, schema dialect/profile not pinned)
- 2 R2 SHOULD-FIX (escape-hatch ergonomics, doc consistency)
- 1 R2 NIT (cursor opacity)
- 5 R3 SHOULD-FIX/NIT (generator pipeline, conformance/experimental separation, HTTP session, Origin null, telemetry-shape contract)

**ADR additions across the loop:** ADR-13, ADR-14, ADR-15, ADR-15a, ADR-15b, ADR-16, ADR-17 (7 new ADRs vs. R0's 12 → final 19 ADRs).

**Gate additions across the loop:** G8a-d (replay budget), G9a-c (`view_at` cursor), G10a-c (TypedDict + signature lockfile), G11 (confidentiality non-goal), G12a-e (HTTP experimental defaults), G13a-d (Schema Profile v0.8 enforcement). 7 → 13 gates.

**Risk additions across the loop:** R8 (surface leakage), R9 (tool collision), R10 (replay burn / state exfil), R11 (token bootstrap argv leak), R12 (privacy expectation gap), R13 (HTTP local-web attack surface), R14 (schema generator drift). 7 → 14 risks.

**Open-question rewrite:** R0's 5 OQs (recall ranking, audit subscribe, in-process token, health window, context propagation) demoted to OQ-6..OQ-10; OQ-1..OQ-5 are now the structural blockers (surface enforcement, MCP rev drift, schema authoring, cursor lifetime, budget calibration).

**Raw transcripts:**
- `review-stage/v0.8.0-adapter-sdk-r1/round1_raw.txt` — R1 6.4/5.5
- `review-stage/v0.8.0-adapter-sdk-r1/round2_raw.txt` — R2 7.99/7.60
- `review-stage/v0.8.0-adapter-sdk-r1/round3_raw.txt` — R3 8.56/8.20 PASS
- `review-stage/v0.8.0-adapter-sdk-r1/REVIEWER_MEMORY.md` — codex's persistent suspicions across rounds
- `review-stage/v0.8.0-adapter-sdk-r1/AUTO_REVIEW.md` — cumulative review log
