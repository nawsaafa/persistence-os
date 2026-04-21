"""Audit handler — hash-chained Merkle log; maps to Fact 8-tuple datoms.

Design notes tied to spec §9 anti-patterns:

- ``audit`` does **not** write to disk synchronously. When constructed with
  ``sink_name=<handler-name>``, it performs ``:audit/emit`` (*masked* from
  itself to prevent re-entry) so a separate archive handler receives each
  entry and may persist asynchronously.
- ``audit`` does **not** call ``time.time()``. It reads ``:clock/now`` under
  mask, so replay mode (mock clock) makes timestamps deterministic.
- Entry ``id`` is a SHA-256 hash of the entry's canonical content — so the
  Merkle chain is cryptographic, not reliant on external uniqueness.
- Both ``verdict='ok'`` (success) and ``verdict='error'`` (failure) entries
  are produced; regulators need the attempted-but-denied trail too (see §3).

The 8-tuple datom schema from agent1-fact-spec.md §1 is reproduced by
:func:`audit_entry_to_datom`; :func:`datom_to_audit_entry` is the inverse.
The emitted wire form conforms to ``:persistence.fact/datom`` as registered
in :mod:`persistence.spec`; that contract is exercised by
``tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec``.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Iterable

from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.effect.runtime import Handler, mask, named, perform
from persistence.effect.verdicts import as_edn as _verdict_as_edn
from persistence.effect.verdicts import as_python as _verdict_as_python


# ---------------------------------------------------------------------------
# AuditEntry — the in-memory record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEntry:
    """One row in the append-only audit log.

    ``id`` is a content-hash of the entry (excluding ``id`` and
    ``prev_hash`` themselves); the log thus forms a Merkle chain where
    tampering is detectable.
    """

    id: str
    prev_hash: str | None
    op: str
    args_hash: str
    verdict: str  # "ok" | "error" | "deny" | "deny-silently" | "require-approval"
    latency_ms: int
    recorded_at: float
    result_hash: str | None = None
    error: str | None = None
    policy_id: str | None = None
    handler_chain: tuple[str, ...] = field(default_factory=tuple)
    principal: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    parent: str | None = None

    def __post_init__(self) -> None:
        """Format invariants on :attr:`op` (ARIS Round 3 P-op-invariants).

        ``op`` must be a well-formed EDN keyword:

        - leading ``:``,
        - at most one forward slash (``/``) — a bare keyword like
          ``:decide`` is valid, and so is ``:llm/call``; but
          ``:llm/call/extra`` breaks the audit datom's ``/ → .``
          encoding,
        - no literal ``.`` anywhere — a literal dot collides with the
          same encoding, so ``datom_to_audit_entry ∘ audit_entry_to_datom``
          would cease to be identity.
        """
        op = self.op
        if not isinstance(op, str):
            raise ValueError(
                f"AuditEntry.op must be a string, got {type(op).__name__}"
            )
        if not op:
            raise ValueError("AuditEntry.op must not be empty")
        if not op.startswith(":"):
            raise ValueError(
                f"AuditEntry.op {op!r} must have a leading colon "
                "(EDN keyword form, e.g. ':llm/call')"
            )
        if op.count("/") > 1:
            raise ValueError(
                f"AuditEntry.op {op!r} has multiple forward slashes — "
                "the audit datom encoding requires at most one '/'"
            )
        if "." in op:
            raise ValueError(
                f"AuditEntry.op {op!r} contains a literal dot — collides "
                "with the audit datom's '/' → '.' encoding"
            )

        # ARIS Round 5 W5-audit-canonicalize — canonicalise sibling
        # keyword-keyed fields at construction time (closes R1 N7 +
        # R3 R4-N1/N2). Mirrors the ``Datom.__post_init__`` pattern
        # from W-wire: the in-memory form is the canonical one; the
        # wire layer uniformly prepends ``":"``. Any input with a
        # leading colon is stripped on construction, so two AuditEntry
        # values that differ only by a leading colon are impossible.
        #
        # - ``policy_id`` is keyword-spec'd on the wire
        #   (``:audit/policy-id``); ``policy_eval.py`` emits bare-string
        #   IDs (``"bankability-v3"``, ``"unknown"``) so the in-memory
        #   form carries the bare string and the wire form prepends.
        #   Canonical internal form: keyword (leading ``":"``). This
        #   reverses the sign from handler_chain/principal because the
        #   ``to_edn`` code path already had a ``":" +`` branch that
        #   tolerated either form; with canonicalisation we guarantee
        #   a single shape so self-conform passes.
        # - ``handler_chain`` and ``principal`` store bare strings
        #   internally; ``to_edn`` prepends colons. ``from_edn`` strips
        #   them back. Canonical internal form: bare (no leading
        #   colon), so ``from_edn ∘ to_edn`` is idempotent.
        #
        # ``lstrip(":")`` (not ``[1:]``) so double-colon inputs
        # idempotently collapse — see R3 R4-N3.

        if self.policy_id is not None and isinstance(self.policy_id, str):
            # ARIS Round 5 W-polish3 W6-canonicalize-harmonize (closes
            # R5 N2 MINOR). Use ``":" + lstrip(":")`` to match sibling
            # fields ``handler_chain`` / ``principal`` keys — idempotent
            # on any number of leading colons (``"::x"`` → ``":x"``).
            # The previous prepend-if-missing branch left multi-colon
            # inputs unchanged, so two AuditEntry values with
            # ``policy_id="::x"`` and ``policy_id=":x"`` had divergent
            # shapes (non-idempotent under repeat construction).
            object.__setattr__(
                self, "policy_id", ":" + self.policy_id.lstrip(":")
            )

        if self.handler_chain is not None:
            # Canonicalise each entry to bare form. ``lstrip(":")`` is
            # idempotent on both bare and pre-keyworded inputs.
            object.__setattr__(
                self,
                "handler_chain",
                tuple(
                    h.lstrip(":") if isinstance(h, str) else h
                    for h in self.handler_chain
                ),
            )

        if isinstance(self.principal, dict):
            # Canonicalise principal keys to bare form. The dict itself
            # is mutable (``field(default_factory=dict)``), so we
            # rebuild in place to propagate to any alias the caller
            # still holds.
            canonical = {
                (k.lstrip(":") if isinstance(k, str) else k): v
                for k, v in self.principal.items()
            }
            # Clear + update to preserve the dict identity (avoids
            # surprising aliasing semantics).
            self.principal.clear()
            self.principal.update(canonical)

    # ---- helpers ----

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def with_fields(self, **changes: Any) -> AuditEntry:
        return dataclasses.replace(self, **changes)

    def to_edn(self) -> dict[str, Any]:
        """Return the EDN wire form that conforms to
        ``:persistence.effect/audit-entry`` (ARIS Round 3 P-audit-conform).

        The spec is aligned with this dataclass's field set, so this is
        the *single producer* of the :audit-entry boundary. Verdicts are
        keyworded via :func:`persistence.effect.verdicts.as_edn`, so a
        Python ``"ok"`` becomes ``":ok"``. The principal dict goes
        through :func:`_principal_to_keyword_map` so bare-string keys
        become EDN keywords. Keys whose value is ``None`` and whose spec
        slot is optional are omitted to keep the wire form tight.
        """
        edn: dict[str, Any] = {
            ":audit/id": self.id,
            ":audit/op": self.op,
            ":audit/args-hash": self.args_hash,
            ":audit/verdict": _verdict_as_edn(self.verdict),
            ":audit/latency-ms": self.latency_ms,
            ":audit/recorded-at": _recorded_at_to_inst(self.recorded_at),
            # ARIS Round 4 W4-handler-chain-wire (closes R1 N6) — keywordify
            # each entry at the wire boundary (symmetric with
            # ``_principal_to_keyword_map``). Production handlers register
            # with bare-string names (``"audit"``, ``"llm"``, etc.); the
            # ``:persistence.effect/audit-entry`` spec requires each chain
            # entry to be an EDN keyword. Idempotent on already-keyworded
            # entries, so mixed chains round-trip cleanly.
            ":audit/handler-chain": _handler_chain_to_keywords(self.handler_chain),
            ":audit/principal": _principal_to_keyword_map(self.principal),
        }
        # Optional fields: include only when meaningful. ``None`` is a
        # valid (maybe) value for :prev-hash, :result-hash, :error etc.;
        # emit it explicitly so downstream readers see the intended slot.
        if self.prev_hash is not None:
            edn[":audit/prev-hash"] = self.prev_hash
        if self.result_hash is not None:
            edn[":audit/result-hash"] = self.result_hash
        if self.error is not None:
            edn[":audit/error"] = self.error
        if self.policy_id is not None:
            # W5 canonicalisation: ``policy_id`` is stored in keyword
            # form (leading ``":"``) at construction time, so the
            # wire emission is a straight passthrough.
            edn[":audit/policy-id"] = self.policy_id
        if self.run_id is not None:
            # run_id comes in as a plain UUID string; conform expects uuid_().
            import uuid as _uuid
            try:
                edn[":audit/run-id"] = _uuid.UUID(self.run_id) if isinstance(self.run_id, str) else self.run_id
            except (ValueError, AttributeError):
                edn[":audit/run-id"] = self.run_id
        if self.parent is not None:
            edn[":audit/parent"] = self.parent
        # Self-conform at output — a malformed AuditEntry fails loudly
        # here instead of polluting the audit log.
        from persistence.spec import conform as _conform
        result = _conform(":persistence.effect/audit-entry", edn)
        if not result.is_ok:
            raise ValueError(
                f"AuditEntry.to_edn produced a non-conformant value: {result}"
            )
        return edn

    @classmethod
    def from_edn(cls, edn: dict[str, Any]) -> AuditEntry:
        """Inverse of :meth:`to_edn` — reconstruct an ``AuditEntry`` from
        its ``:persistence.effect/audit-entry`` wire form.

        Closes ARIS Round 3 R3 N6: Phase-2 regulator-replay needs the
        audit-entry-wire → AuditEntry inverse (the existing
        ``datom_to_audit_entry`` is a different shape — it inverts the
        datom wire form, not the audit-entry wire form). This is the
        symmetric producer: keyworded entries on the wire come back as
        bare-string Python-native form.
        """
        # recorded_at → float seconds since epoch (the in-memory representation
        # the dataclass uses; ``to_edn`` projects it to a tz-aware datetime).
        recorded_at_raw = edn.get(":audit/recorded-at")
        if isinstance(recorded_at_raw, _dt.datetime):
            recorded_at = recorded_at_raw.timestamp()
        else:
            recorded_at = float(recorded_at_raw)  # type: ignore[arg-type]

        # run_id: uuid.UUID on the wire → str on the Python side.
        run_id_raw = edn.get(":audit/run-id")
        if run_id_raw is not None:
            run_id = str(run_id_raw)
        else:
            run_id = None

        # Verdict: ":ok" on wire → "ok" on Python.
        verdict = _verdict_as_python(edn[":audit/verdict"])

        return cls(
            id=edn[":audit/id"],
            prev_hash=edn.get(":audit/prev-hash"),
            op=edn[":audit/op"],
            args_hash=edn[":audit/args-hash"],
            verdict=verdict,
            latency_ms=edn[":audit/latency-ms"],
            recorded_at=recorded_at,
            result_hash=edn.get(":audit/result-hash"),
            error=edn.get(":audit/error"),
            policy_id=edn.get(":audit/policy-id"),
            handler_chain=_handler_chain_from_keywords(
                edn.get(":audit/handler-chain", ())
            ),
            principal=_keyword_map_to_principal(edn.get(":audit/principal", {})),
            run_id=run_id,
            parent=edn.get(":audit/parent"),
        )


# ---------------------------------------------------------------------------
# ID / chain computation
# ---------------------------------------------------------------------------


def _content_hash(entry_content: dict[str, Any]) -> str:
    """sha256:... of the canonical JSON of the *content* fields (excluding id)."""
    return canonical_hash(entry_content)


def _canonicalise_content(content: dict[str, Any]) -> dict[str, Any]:
    """Mirror ``AuditEntry.__post_init__``'s canonicalisation on a *dict*.

    ARIS Round 5 W-polish3 W6-factory-canonicalize (closes R5 N1 MAJOR).

    W-polish2 introduced canonicalisation of ``policy_id`` /
    ``handler_chain`` / ``principal`` inside ``AuditEntry.__post_init__``,
    but ``make_audit_handler`` was hashing the *pre-canonical* content
    before constructing the entry. The stored ``entry.id`` thus differed
    from what ``verify_chain`` recomputed (which runs against the
    *canonical* ``entry.to_dict()``), so every production chain where
    ``policy_id`` arrived bare — the shape ``policy_eval.py`` emits —
    failed the Merkle check.

    This helper is the dict-level twin of the dataclass rules:

    - ``policy_id`` → keyword form: ``":" + lstrip(":")`` (harmonised
      with sibling fields in R5 N2; idempotent on ``"::x"``).
    - ``handler_chain`` → tuple of bare strings (``lstrip(":")`` each).
    - ``principal`` keys → bare strings (``lstrip(":")`` each).

    The helper is pure: it copies the dict and only rewrites the three
    known keyword-bearing slots. Unknown keys pass through untouched so
    adding a new field to ``AuditEntry`` doesn't silently break the
    factory hash.
    """
    out = dict(content)
    pid = out.get("policy_id")
    if isinstance(pid, str):
        out["policy_id"] = ":" + pid.lstrip(":")
    chain = out.get("handler_chain")
    if chain is not None:
        out["handler_chain"] = tuple(
            h.lstrip(":") if isinstance(h, str) else h for h in chain
        )
    principal = out.get("principal")
    if isinstance(principal, dict):
        out["principal"] = {
            (k.lstrip(":") if isinstance(k, str) else k): v
            for k, v in principal.items()
        }
    return out


def verify_chain(entries: Iterable[AuditEntry]) -> bool:
    """True iff every entry's id is the content hash of its fields AND
    prev_hash references the previous entry's id.
    """
    prev: str | None = None
    for entry in entries:
        # Recompute content hash.
        d = entry.to_dict()
        d.pop("id")
        expected_id = _content_hash(d)
        if entry.id != expected_id:
            return False
        if entry.prev_hash != prev:
            return False
        prev = entry.id
    return True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_audit_handler(
    entries: list[AuditEntry],
    *,
    wraps: Iterable[str] = (":llm/call",),
    sink_name: str | None = None,
    run_id: str | None = None,
    principal: dict[str, Any] | None = None,
    policy_id: str | None = None,
) -> Handler:
    """Return an audit handler that appends to ``entries``.

    Parameters
    ----------
    entries
        The caller-owned log. Tests pass a fresh list; production code
        passes a list backed by a Ring buffer or file writer.
    wraps
        Ops this handler audits. Typically the stateful ones: :llm/call,
        :tool/call, :decide, :mem/write, :emit-artifact, :net/fetch.
    sink_name
        If given, each entry is also forwarded to the named handler via
        ``:audit/emit`` (masked). Spec §9 anti-pattern avoidance.
    run_id, principal, policy_id
        Fields copied into every entry.
    """
    audit_name = "audit"

    def clause(args: dict[str, Any], k, ctx) -> Any:
        # --- pre-call metadata
        args_hash = canonical_hash(args)
        # Masked so clock/now doesn't re-enter the audit handler (which might
        # itself wrap :clock/now if the caller puts it in wraps).
        with mask(audit_name):
            t0 = perform(":clock/now")["ts"]

        op_name = ctx["_current_op"]  # set per-dispatch below
        result_hash: str | None = None
        error_str: str | None = None
        verdict = "ok"
        raised: BaseException | None = None
        try:
            result = k(args)
            if result is not None:
                result_hash = canonical_hash(result)
            return result
        except BaseException as exc:  # noqa: BLE001
            verdict = "error"
            error_str = f"{type(exc).__name__}: {exc}"
            raised = exc
            raise
        finally:
            with mask(audit_name):
                t1 = perform(":clock/now")["ts"]
            latency_ms = max(0, int((t1 - t0) * 1000))
            prev_hash = ctx["entries"][-1].id if ctx["entries"] else None
            content: dict[str, Any] = {
                "prev_hash": prev_hash,
                "op": op_name,
                "args_hash": args_hash,
                "verdict": verdict,
                "latency_ms": latency_ms,
                "recorded_at": t1,
                "result_hash": result_hash,
                "error": error_str,
                "policy_id": ctx.get("policy_id"),
                "handler_chain": tuple(ctx.get("handler_chain", ())),
                "principal": dict(ctx.get("principal", {})),
                "run_id": ctx.get("run_id"),
                "parent": prev_hash,
            }
            # ARIS Round 5 W-polish3 W6-factory-canonicalize (closes R5
            # N1 MAJOR). W-polish2's ``__post_init__`` canonicalises
            # ``policy_id`` / ``handler_chain`` / ``principal`` on the
            # dataclass side; we must mirror that canonicalisation on
            # the dict side *before* hashing so ``entry.id`` == the hash
            # of ``entry.to_dict()`` (which ``verify_chain`` recomputes).
            canonical_content = _canonicalise_content(content)
            entry_id = _content_hash(canonical_content)
            entry = AuditEntry(id=entry_id, **canonical_content)
            ctx["entries"].append(entry)
            # Drain to named sink if configured.
            sink = ctx.get("sink_name")
            if sink:
                with mask(audit_name):
                    named(sink, ":audit/emit", kind="audit-entry", payload=entry.to_dict())
            # Swallow raised? No — re-raise path handled above.

    # Dispatch shim: we need op name inside the clause but the clause
    # signature is (args, k, ctx). We capture op by wrapping per-op.
    clauses: dict[str, Any] = {}

    def make_op_clause(op_name: str):
        def op_clause(args, k, ctx):
            ctx["_current_op"] = op_name
            return clause(args, k, ctx)

        return op_clause

    for op_name in wraps:
        clauses[op_name] = make_op_clause(op_name)

    return Handler(
        name=audit_name,
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "entries": entries,
            "sink_name": sink_name,
            "run_id": run_id,
            "principal": principal or {},
            "policy_id": policy_id,
            "handler_chain": (),
        },
    )


# ---------------------------------------------------------------------------
# Datom conversion — Fact spec §1, 8-tuple
# ---------------------------------------------------------------------------


def _recorded_at_to_inst(recorded_at: float | _dt.datetime) -> _dt.datetime:
    """Coerce the audit handler's ``recorded_at`` to a tz-aware datetime.

    The audit handler reads ``:clock/now`` which the system clock handler
    returns as a Unix epoch float. The canonical ``:datom/tx-time`` spec is
    :func:`persistence.spec.inst` (tz-aware). Fix is on the effect side,
    not the spec side: wall-clock instants are the correct representation.

    Accepts already-tz-aware datetimes for forward compatibility with a
    future clock handler that returns datetimes directly.
    """
    if isinstance(recorded_at, _dt.datetime):
        if recorded_at.tzinfo is None:
            raise ValueError(
                "recorded_at datetime is naive; clock handler must emit "
                "tz-aware instants"
            )
        return recorded_at
    # float / int — treat as Unix epoch seconds (clock/now convention).
    return _dt.datetime.fromtimestamp(float(recorded_at), tz=_dt.timezone.utc)


def _principal_to_keyword_map(principal: dict[str, Any]) -> dict[str, Any]:
    """Prepend ``":"`` to each string key in the principal dict.

    ARIS Round 5 W5-audit-canonicalize: with ``AuditEntry.__post_init__``
    canonicalising principal keys to bare form, this helper is now the
    single-direction wire producer — every key is guaranteed to be bare
    on input. The branch on "already colon" is gone.

    The canonical ``:audit/principal`` spec is
    ``map_of(_keyword_spec, _any_value)`` — EDN-keyword keys.
    """
    return {
        (":" + k if isinstance(k, str) else k): v for k, v in principal.items()
    }


def _keyword_map_to_principal(keyworded: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`_principal_to_keyword_map`.

    With W5 canonicalisation, ``AuditEntry.__post_init__`` strips keys
    to bare form on construction, so this helper's ``lstrip(":")`` is
    still defensive on pre-keyworded wire input and idempotent on
    bare-string input.
    """
    return {
        (k.lstrip(":") if isinstance(k, str) else k): v
        for k, v in keyworded.items()
    }


def _handler_chain_to_keywords(chain: Iterable[str]) -> list[str]:
    """Prepend ``":"`` to each bare-string handler name in the chain.

    ARIS Round 5 W5-audit-canonicalize: with ``AuditEntry.__post_init__``
    canonicalising ``handler_chain`` to bare form, this helper is now
    the single-direction wire producer — every entry is guaranteed to
    be bare on input. The branch on "already colon" is gone.

    The canonical ``:audit/handler-chain`` spec is
    ``seq_of(_keyword_spec)`` — each entry must be an EDN keyword.
    Production handlers are registered with bare-string names
    (``"audit"``, ``"llm"``, ``"tool"``, etc.); leading colons are
    added at the wire boundary (ARIS Round 4 W4-handler-chain-wire
    closed R1 N6 with serialisation-time branching; W5 moves the
    normalisation to construction so the branch is no longer needed).
    """
    return [":" + h if isinstance(h, str) else h for h in chain]


def _handler_chain_from_keywords(chain: Iterable[str]) -> tuple[str, ...]:
    """Inverse of :func:`_handler_chain_to_keywords` — strip leading colons
    back to the Python-native bare-string form. Defensive on both bare
    and keyworded input (uses ``lstrip`` for idempotency on edge
    cases like double-colons).
    """
    return tuple(
        h.lstrip(":") if isinstance(h, str) else h for h in chain
    )


def audit_entry_to_datom(entry: AuditEntry) -> dict[str, Any]:
    """Convert an AuditEntry to the 8-tuple datom shape from agent1-fact-spec §1.

    The emitted dict is the *wire form* — it conforms to
    ``:persistence.fact/datom`` as registered in :mod:`persistence.spec`. That
    contract is the bridge from the effect module to the fact store, and
    this function is the single producer of that bridge.

    Mapping:

    - ``:datom/e``              → run-id (UUID string) when set, else the
                                  entry's content hash (sha256:...). The
                                  canonical spec accepts either via
                                  ``or_(uuid_(), _sha256_spec)``.
    - ``:datom/a``              → ``:audit/<op>`` as an EDN keyword with
                                  leading colon (e.g. ``:audit/llm/call``).
    - ``:datom/v``              → dict with verdict, args-hash, result-hash,
                                  latency-ms, error.
    - ``:datom/tx``             → entry.id (sha256 content hash). Spec accepts
                                  this via ``or_(int_(), _sha256_spec)``.
    - ``:datom/tx-time``        → recorded_at as tz-aware datetime (UTC).
    - ``:datom/valid-from``     → same as tx-time (we learn the fact then).
    - ``:datom/valid-to``       → None (open interval; audit is append-only).
    - ``:datom/op``             → ``":assert"`` (EDN keyword).
    - ``:datom/provenance``     → EDN-keyword-keyed map. Verdict in
                                  :datom/v is converted via
                                  :func:`persistence.effect.verdicts.as_edn`
                                  at the wire boundary — no test needs
                                  changes because :datom/v is not itself
                                  the :audit/verdict slot.
    - ``:datom/invalidated-by`` → None.
    """
    inst = _recorded_at_to_inst(entry.recorded_at)
    # EDN keyword regex allows a single namespace slash; the op name may itself
    # contain a slash (e.g. "llm/call" or the colon-prefixed ":llm/call").
    # Strip any leading colon on the op, then encode the inner slash as a dot
    # so ":audit/llm.call" is a valid keyword. datom_to_audit_entry is the
    # symmetric decoder — these two are co-inverse only because no op in the
    # catalog contains a literal ``.``.
    op_bare = entry.op.lstrip(":")
    a_keyword = ":audit/" + op_bare.replace("/", ".")
    provenance: dict[str, Any] = {
        ":source": ":persistence.effect.audit",
        ":confidence": 1.0,
        ":signature": entry.id,
        ":prev-hash": entry.prev_hash,
        ":handler-chain": list(entry.handler_chain),
        ":policy-id": entry.policy_id,
        ":principal": _principal_to_keyword_map(entry.principal),
    }
    if entry.run_id is not None:
        provenance[":episode"] = entry.run_id
    value = {
        "verdict": _verdict_as_edn(entry.verdict),
        "args_hash": entry.args_hash,
        "result_hash": entry.result_hash,
        "latency_ms": entry.latency_ms,
        "error": entry.error,
    }
    datom = {
        ":datom/e": entry.run_id or entry.id,
        ":datom/a": a_keyword,
        ":datom/v": value,
        ":datom/tx": entry.id,
        ":datom/tx-time": inst,
        ":datom/valid-from": inst,
        ":datom/valid-to": None,
        ":datom/op": ":assert",
        ":datom/provenance": provenance,
        ":datom/invalidated-by": None,
    }
    # Self-conform at the wire boundary (ARIS Round 3 P-audit-conform).
    # Symmetric with ``fact/wire.py::datom_to_wire`` which has done this
    # since Round 2. A malformed datom fails loudly here instead of
    # travelling quietly into the fact store.
    from persistence.spec import conform as _conform
    result = _conform(":persistence.fact/datom", datom)
    if not result.is_ok:
        raise ValueError(
            f"audit_entry_to_datom produced a non-conformant datom: {result}"
        )
    return datom


def datom_to_audit_entry(datom: dict[str, Any]) -> AuditEntry:
    """Inverse of :func:`audit_entry_to_datom`.

    Accepts the EDN-keyword wire form (leading-colon keys). The verdict in
    ``:datom/v`` is translated back to the bare-string Python form via
    :func:`persistence.effect.verdicts.as_python`.
    """
    provenance = datom[":datom/provenance"]
    value = datom[":datom/v"]
    a = datom[":datom/a"]
    assert a.startswith(":audit/"), f"not an audit datom: {a}"
    # Decode ``llm.call`` → ``:llm/call`` — see audit_entry_to_datom. The
    # original op was leading-colon EDN keyword form; re-add the colon.
    op = ":" + a[len(":audit/") :].replace(".", "/")
    recorded_at = datom[":datom/tx-time"]
    if isinstance(recorded_at, _dt.datetime):
        recorded_at = recorded_at.timestamp()
    return AuditEntry(
        id=datom[":datom/tx"],
        prev_hash=provenance.get(":prev-hash"),
        op=op,
        args_hash=value["args_hash"],
        verdict=_verdict_as_python(value["verdict"]),
        latency_ms=value["latency_ms"],
        recorded_at=recorded_at,
        result_hash=value.get("result_hash"),
        error=value.get("error"),
        policy_id=provenance.get(":policy-id"),
        handler_chain=tuple(provenance.get(":handler-chain", ())),
        principal=_keyword_map_to_principal(provenance.get(":principal", {})),
        run_id=provenance.get(":episode"),
        parent=provenance.get(":prev-hash"),
    )


# Re-export canonical_dumps so callers importing this module don't need
# to reach into the canonical helper.
__all__ = [
    "AuditEntry",
    "audit_entry_to_datom",
    "canonical_dumps",
    "datom_to_audit_entry",
    "make_audit_handler",
    "verify_chain",
]
