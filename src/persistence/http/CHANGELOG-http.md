# persistence.http CHANGELOG

## Phase 2.1c.6 — 2026-05-05 (audit chain wiring for claim emits + blob puts)

### Added
- `POST /v1/claim/emit` now performs `s.effect.perform(":claim/emit", ...)`
  AFTER `s.fact.transact(datoms)` to anchor the canonical audit chain.
  `audit_chain_head` is now a real `sha256:<hex>` string (was the W3
  placeholder `None` in 2.1c).
- `POST /v1/blob/put` now performs `s.effect.perform(":blob/put", ...)`
  after the fact transact. `BlobPutResponse` gains `audit_chain_head: str`.
- New shared helper `src/persistence/http/routes/_audit.py::extract_audit_chain_head`
  used by both routes.
- 6 new test gates: G1 (xfail flip), G2 (args_hash assertion), G3 (replay
  byte-identity), G4 (audit-perform failure semantics), G6 (blob put
  parity), G7 (audit=False ergonomics). G8 (concurrent chain integrity)
  updated from the 2.1c placeholder version.

### Changed
- `ClaimEmitResponse.audit_chain_head: Optional[str] = None` → `str`.
  Downstream consumers MUST handle the type change. The 2.1c R1.1 W3
  honest-rescope is closed; `audit_chain_head` is now a real audit head.
- `BlobPutResponse.audit_chain_head: str` (new field).

### Removed
- `@pytest.mark.xfail(strict=True)` decorator from
  `tests/http/test_audit_chain.py::test_audit_chain_head_advances_with_emits`.
  The strict-xfail flips PASS, marking 2.1c.6 as shipped per the
  falsifiable acceptance signal contract.

### Notes
- `:claim/emit` audit args do NOT include `session_id` (a per-claim
  attribute, not a batch attribute). `:blob/put` audit args DO include
  `session_id` (route boundary owns a single session via X-Session-Id
  header). See design § 3.4.
- `AuditEntry` persists `args_hash` only (not raw args). G2 test asserts
  `args_hash == canonical_hash(expected_args)` via
  `from persistence.effect.canonical import canonical_hash`.

### Refs
- Design: `docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md`
- ARIS: R1 PASS-WITH-FIXES (8.14/7.1) → R1.1 fold → R1.1 lite PASS (8.16/7.8)

## v0.9.0a1 (unreleased) — Phase 2.1c `persistence.http` module

Phase 2.1c ships the `persistence.http` package — a FastAPI-based
HTTP gateway over the persistence substrate. Four routes expose
claim emission, claim query, blob storage, and blob retrieval.
The module is optional (install group `persistence[http]`) and is
consumer-side only: it imports from `persistence.sdk` and
`persistence.claim`, never from raw substrate modules.

### Added

- **`build_app(substrate, blob_dir)` factory** (`server.py`). Returns
  a configured `FastAPI` instance. Registers all four routes,
  installs exception handlers for `RequestValidationError` and
  `HTTPException`, and pins the uniform error envelope across all
  4xx responses.
- **Bearer auth via `require_auth()` dependency** (`auth.py`).
  Validates `Authorization: Bearer <token>` against a substrate-stored
  token. Opt-in loopback bypass controlled by the
  `PERSISTENCE_HTTP_LOOPBACK_BYPASS=1` environment variable (default
  `0` — bypass disabled). The server refuses to start when both the
  API token and the loopback bypass are absent (no anonymous
  non-loopback access).
- **Socket-peer-only loopback detection** (`auth.py`). Loopback
  classification reads `request.client.host` exclusively. The headers
  `X-Forwarded-For`, `Forwarded`, and `X-Real-IP` are explicitly
  **ignored** — proxy header trust is a deployment configuration
  concern, not an authentication concern. IPv4 loopback (`127.0.0.1`)
  and IPv6 loopback (`::1`) are both recognized; Tailscale CGNAT
  ranges are not treated as loopback.
- **Uvicorn entry with `proxy_headers=False, forwarded_allow_ips=""`
  pin** (`__main__.py`). Closes ARIS R1.2 finding N3: uvicorn's
  default `proxy_headers=True` would silently overwrite
  `request.client` with forwarded headers, undermining the socket-
  peer authentication model. The pin is unconditional — callers that
  need proxy header trust must sit behind an explicit reverse proxy
  they control.
- **Pydantic v2 request/response schemas** (`schemas.py`). Covers
  `ClaimEmitRequest`, `ClaimEmitResponse`, `ClaimQueryResponse`,
  `ClaimRecord`, `BlobPutResponse`, and the closed error envelope
  `ErrorResponse(error: str, detail: str)`. All schema classes are
  `@experimental("v0.9.x")`.
- **Filesystem CAS blob store with atomic `os.link` writes**
  (`blob_store.py`). Content-addressed by `sha256:<hex>`. Writes go
  through a temp file + `os.link` (or `shutil.copy2` fallback on
  cross-device), making concurrent duplicate puts idempotent. Two-
  character sharding on the hex digest (`<hash[:2]>/<hash>`) keeps
  directory fan-out bounded.
- **4 routes**:
  - `POST /v1/claim/emit` — validates each claim via
    `validate_attrs`, writes datoms via `substrate.fact.transact`,
    returns `claim_ids` list. Batch is atomic: one bad claim in the
    list rejects the whole request before any datom write. Body size
    cap at 1 MiB (`MAX_EMIT_BYTES`).
  - `GET /v1/claim/query` — filters datoms by `session_id` (required)
    and optional `kind`. Restores the colon-prefixed kind convention
    when building `ClaimRecord.kind` (see forced deviations below).
    Paginates via `since` cursor + `limit` (default 100, max 500).
  - `POST /v1/blob/put` — stores raw bytes in the filesystem CAS,
    transacts a `:claim/blob-put` datom, returns `hash`, `size`,
    `duplicate` flag. Body size cap 64 MiB.
  - `GET /v1/blob/get/{hash}` — retrieves bytes by `sha256:` hash.
    Returns 404 with uniform envelope when absent.
- **Uniform error envelope `{"error": <kind>, "detail": <human>}`**
  across all 4xx responses (`server.py`). Error kind is a closed enum
  per route; the envelope shape is locked so clients can branch on
  `error` without parsing `detail`.
- **2.1c.5 xfail-strict acceptance signal** (`tests/http/test_auth.py`).
  `test_non_loopback_without_caller_signature_rejected_even_with_valid_bearer`
  is marked `@pytest.mark.xfail(strict=True, reason="2.1c.5 — caller
  identity attestation")`. It flips to PASS when Phase 2.1c.5 lands
  Ed25519 caller attestation; at that point `strict=True` forces a CI
  failure if the marker is not removed.
- **2.1c.6 xfail-strict acceptance signal** (`tests/http/test_audit_chain.py`).
  `test_audit_chain_head_advances_with_emits` is marked
  `xfail(strict=True, reason="2.1c.6 — audit chain wiring")`. It
  flips to PASS when `s.fact.transact` is wired to the canonical
  audit chain in Phase 2.1c.6.

### Optional install

`pip install persistence[http]` pulls:
- `fastapi>=0.115`
- `uvicorn[standard]>=0.32`
- `pydantic>=2.9`

Substrate-only consumers do not need these packages. The `http`
extra is additive and does not change any existing public surface.

### Forced spec deviations during implementation

Nine deviations from the design spec were resolved at implementation
time:

1. **`s.fact.transact` adapter** — design spec referenced a
   `substrate.claim.transact` surface that does not exist. Routes
   call `substrate.fact.transact(datoms)` per the DB.transact
   contract. Documented in `routes/claim.py` module docstring.
2. **`Datom.a` colon-stripping** — `Datom.__post_init__` strips the
   leading `:` from attribute names on construction, so stored
   `datom.a` values are bare (`"claim/tool-exec"`, not
   `":claim/tool-exec"`). `CLAIM_KINDS` uses the colon-prefixed
   convention. Query logic strips the colon before comparing against
   `datom.a` and restores it when building `ClaimRecord.kind`.
   A `_BARE_CLAIM_KINDS` frozenset computed from `CLAIM_KINDS` is
   the single source of truth for this translation (T10 deviation,
   documented in `routes/claim.py`).
3. **Body size via raw read** — FastAPI parses the request body as
   JSON automatically via the typed `req` parameter, making pre-parse
   size checks impossible through the standard path. The emit route
   reads the raw body via `await request.body()` before parsing;
   raises 413 if `len(raw_body) > MAX_EMIT_BYTES`. A separate
   `RequestSizeLimitMiddleware` was considered but the inline raw-
   read pattern keeps the route self-contained (T9 deviation).
4. **`malformed_body` / `json_invalid` heuristic** — `RequestValidationError`
   from FastAPI covers both JSON parse failures and schema validation
   failures. The exception handler in `server.py` inspects the
   `type == "json_invalid"` field on individual error dicts; JSON
   parse failures map to `400 malformed_body`, schema failures map
   to `422 attrs_validation_failed`. This replaces a simple
   status-code check that would have mapped all validation errors
   to 422 (T13 deviation, closed in R1.1 fix-pass `6ef4a67`).
5. **`audit_chain_head` always `None`** — the design expected routes
   to attach an audit chain head hash to responses. `s.fact.transact`
   in Phase 2.1c bypasses the canonical audit chain; `audit_chain_head`
   returns `None` until Phase 2.1c.6 wires the substrate-side
   audit path (see forward references).
6. **Wall-clock `dt.datetime.now` for provenance timestamps** —
   routes use `dt.datetime.now(dt.timezone.utc)` for datom
   `tx_time` with `# noqa: wall-clock` markers. This routes through
   `:sys/now` in Phase 2.4a when the effect router is available. The
   same pattern is established by `coder/_session.py` (Phase 2.1b
   precedent, design § 6).
7. **Wall-clock `uuid.uuid4()` for entity IDs** — entity IDs are
   generated with `uuid.uuid4().hex` with `# noqa: wall-clock`
   markers, matching the existing txn module precedent.
8. **`ClaimQueryResponse.next_since` pagination** — the design did not
   specify a pagination cursor shape. `next_since` is the `tx` integer
   of the last returned datom, usable as the `since` query parameter
   on the next call. Empty results return `next_since=None`.
9. **`test_http_curated_sdk_discipline.py` test** — `tests/test_curated_sdk_discipline.py`
   gains one new check (`test_no_s_escape_in_http_module`) covering the
   `http/` module in addition to the `coder/` module already covered in
   Phase 2.1b (T18, G10).

### Test surface

- `tests/http/test_auth.py` (18 tests) — bearer auth, loopback bypass
  on/off, socket-peer-only detection, IPv4/IPv6/Tailscale classification,
  all three ignored-header probes, 2.1c.5 xfail-strict acceptance signal.
- `tests/http/test_blob_store.py` (6 tests) — `put` happy path,
  idempotent duplicate, sharded path structure, `get` hit/miss,
  malformed hash rejection.
- `tests/http/test_schemas.py` (4 tests) — `ClaimEmitRequest` minimal
  + empty-list rejection, `ClaimEmitResponse` shape, `ErrorResponse`
  locked shape.
- `tests/http/test_routes_claim_emit.py` (8 tests) — single-claim
  happy path, multi-claim atomic, one-bad-claim rejects all, `:fact/*`
  kind rejection, unknown kind rejection, schema violation 422,
  oversize 413, malformed body 400.
- `tests/http/test_routes_claim_query.py` (7 tests) — missing
  `session_id`, session-filtered results, kind filter accepts claim
  kind, rejects `:fact/*`, rejects unknown kind, pagination via
  `next_since`, mixed-namespace only-returns-claims invariant.
- `tests/http/test_routes_blob_put.py` (5 tests) — first put returns
  `duplicate=False`, idempotent second put returns `duplicate=True`,
  two datoms emitted on duplicate, missing `session_id` rejected,
  oversize body 413.
- `tests/http/test_routes_blob_get.py` (4 tests) — get bytes for known
  hash, 404 uniform envelope for unknown hash, 400 malformed hash,
  unauthenticated rejection.
- `tests/http/test_audit_chain.py` (3 tests) — mixed-namespace chain
  integrity, 2.1c.6 xfail-strict acceptance signal, concurrent emits
  serialize distinct datoms.
- `tests/http/test_main_smoke.py` (2 tests) — uvicorn invoked with
  `--no-proxy-headers`, hermetic subprocess smoke.
- `tests/http/test_roundtrip.py` (3 tests) — emit/query byte-for-byte
  roundtrip, blob put-emit-reference-get roundtrip, 100-emit fuzz.
- `tests/http/test_props.py` (3 tests, Hypothesis `@max_examples=50`) —
  emit/query roundtrip property, blob put idempotency property,
  pagination union property.

Total: **60 tests** in `tests/http/`. Suite delta accumulated with
Phase 2.1c claim totals; full suite at merge-commit HEAD:
2301 passed, 38 skipped, 10 xfailed, 0 failed.

### Forward references

- **Phase 2.1c.5** — `CallerIdentity.attest` Ed25519 verification.
  The `test_non_loopback_without_caller_signature_rejected_even_with_valid_bearer`
  xfail-strict marker is the acceptance signal.
  Design ground truth (when written):
  `docs/plans/2026-05-04-phase-2.1c.5-caller-identity-design.md`.
- **Phase 2.1c.6** — substrate-side audit-chain wiring: `s.fact.transact`
  emits canonical audit entries. The `test_audit_chain_head_advances_with_emits`
  xfail-strict marker is the acceptance signal.
  Design ground truth (when written):
  `docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md`.
- **Phase 2.4a** — wall-clock callsites (`dt.datetime.now`,
  `uuid.uuid4`) route through `:sys/now` / `:sys/random` effect ops
  per design § 6. The `# noqa: wall-clock` markers are the
  forward-pointer; no separate xfail is needed since these are
  provenance details, not behavioral gates.

### ARIS gate

Design ARIS R2 PASS at mean **8.24 / min 7.6** (codex standard-mode,
two-round trajectory):
`docs/plans/2026-05-04-phase-2.1c-context-substrate-design.md`.

Impl ARIS R1.1 PASS at mean **8.22 / min 7.8** (codex high-mode):
five IMPORTANT findings folded clean across the R1.1 fix-pass
(`6ef4a67`). The pre-fix rescore was R1.0 at mean 7.98 / min 7.6
(below the 8.0 / 7.5 gate); all five findings were classified as
IMPORTANT and corrected in the fix-pass before marking the impl
ARIS PASS.
