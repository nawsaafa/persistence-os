"""Capability ADT + opaque-token machinery for the REPL (Module 7).

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-3 (capability tokens), 6.1-6.4 (token format / issuance /
validation / revocation), and 10 (D1 task).

Tokens are opaque random 256-bit values (``secrets.token_urlsafe(32)``)
prefixed with ``persistence.repl/``. The raw token is NEVER persisted;
only ``token_id = sha256(token_str)[:16]`` lands in the fact store. A
``CapabilitySet`` is serialized as one canonical-JSON string and stored
as one datom ``(e=token_id, a="repl/cap-set", v=<canonical-JSON>)``.

Validation reads the entity at ``db.as_of(now)``. Revocation writes one
``(e=token_id, a="repl/revoked", v=True)`` datom; the entity projection
then carries that flag, and ``validate_token`` rejects the lookup. The
canonical-JSON encoding round-trips byte-identically through
``persistence.effect.canonical.canonical_dumps`` for any equal
``CapabilitySet`` (pinned by a Hypothesis property test).

Forward-compat: the closed set ``QUALIFIERS_BY_OP`` is the v0.7.0a1
authoritative shape. v0.7.x bumps must explicitly add to it; an
``UnknownCapability`` raises on construction of a ``Capability`` with a
qualifier the current closed set does not list. This pins the
"v0.7.0a1 token does not silently grant a v0.7.x qualifier" claim from
§12 risks.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from persistence.effect.canonical import canonical_dumps


# ---------------------------------------------------------------------------
# Closed capability set
# ---------------------------------------------------------------------------
# The authoritative (op, qualifier) closed set in v0.7.0a1. Adding a new
# qualifier is a v0.7.x explicit change — a v0.7.0a1 ``Capability`` cannot
# silently grant one. See §12 risks "Cap-set forward-compat" and the test
# ``test_caps_forward_compat_strict``.
OP_NAMES: tuple[str, ...] = ("inspect", "edit", "rewind", "branch", "auth", "coder")

QUALIFIERS_BY_OP: dict[str, frozenset[str]] = {
    "inspect": frozenset({"read", "audit-tail"}),
    "edit": frozenset({"write", "propose-only"}),
    "rewind": frozenset({"any", "branch-only"}),
    "branch": frozenset({"fork", "fork-from-cursor"}),
    "auth": frozenset({"login"}),
    # Phase 2.3d LD-3 (R0-fold B5): coder steering capability — extends the
    # ADR-3 closed Capability primitive so a REPL operator can grant
    # read-only / write-only / union access to a _CoderSteeringSession.
    # Mapping (from design doc LD3 table):
    #   pause/resume/snapshot/context_at -> (coder, read)
    #   branch/fold/commit               -> (coder, write)
    #   union                             -> (coder, any) grants both
    "coder": frozenset({"read", "write", "any"}),
}

ALL_CAPS: frozenset[tuple[str, str]] = frozenset(
    (op, q) for op, qs in QUALIFIERS_BY_OP.items() for q in qs
)


class UnknownCapability(ValueError):
    """Raised when a ``Capability`` is constructed with an ``(op, qualifier)``
    pair not in the v0.7.0a1 closed set ``ALL_CAPS``.
    """


# ---------------------------------------------------------------------------
# Capability ADT
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Capability:
    """One ``(op, qualifier)`` pair. Frozen + hashable for ``frozenset``
    membership in ``CapabilitySet.caps``.
    """

    op: str
    qualifier: str

    def __post_init__(self) -> None:
        if (self.op, self.qualifier) not in ALL_CAPS:
            raise UnknownCapability(
                f"Unknown capability: ({self.op!r}, {self.qualifier!r}). "
                f"v0.7.0a1 closed set: "
                f"{sorted((op, q) for op, q in ALL_CAPS)}"
            )


# ---------------------------------------------------------------------------
# CapabilitySet
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CapabilitySet:
    """A frozen set of ``Capability``s plus expiry + label.

    Serialized via ``to_canonical()`` + ``canonical_dumps`` to a single
    canonical-JSON string for the fact-store ``repl/cap-set`` datom.
    Round-trip ``from_canonical_dict(json.loads(canonical_dumps(cs.to_canonical())))``
    is byte-identical to ``cs`` (pinned by a Hypothesis property test).
    """

    caps: frozenset[Capability]
    expires_at: datetime | None = None
    label: str = ""

    def has(self, op: str, qualifier: str) -> bool:
        """Membership check. Constructs a ``Capability`` first so an
        unknown ``(op, qualifier)`` raises ``UnknownCapability`` rather
        than silently returning ``False``.
        """
        return Capability(op, qualifier) in self.caps

    def is_expired(self, now: datetime) -> bool:
        """``True`` iff ``expires_at`` is set and ``now >= expires_at``.
        Boundary semantics: the token expires AT ``expires_at`` (inclusive
        — ``now == expires_at`` is already expired).
        """
        return self.expires_at is not None and now >= self.expires_at

    def to_canonical(self) -> dict:
        """Return a plain dict suitable for ``canonical_dumps``.

        The ``caps`` list is sorted by ``(op, qualifier)`` so the encoding
        is referentially transparent: equal ``CapabilitySet``s produce
        byte-identical canonical-JSON strings (W1.F pin).
        """
        return {
            "caps": sorted(
                [{"op": c.op, "qualifier": c.qualifier} for c in self.caps],
                key=lambda d: (d["op"], d["qualifier"]),
            ),
            "expires_at_iso": (
                self.expires_at.isoformat() if self.expires_at else None
            ),
            "label": self.label,
        }

    @classmethod
    def from_canonical_dict(cls, d: dict) -> "CapabilitySet":
        """Inverse of ``to_canonical()``. Round-trip byte-identical for
        any well-formed input — pinned by Hypothesis property test.
        """
        caps = frozenset(
            Capability(c["op"], c["qualifier"]) for c in d["caps"]
        )
        expires_at_iso = d.get("expires_at_iso")
        expires_at = (
            datetime.fromisoformat(expires_at_iso) if expires_at_iso else None
        )
        return cls(caps=caps, expires_at=expires_at, label=d.get("label", ""))


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
def _token_id(token_str: str) -> str:
    """Derive the 16-hex token_id from the raw token string.

    Single-helper rule (W2 MINOR-7): EVERY computation of the token_id
    routes through this function. The raw ``token_str`` is never
    persisted; only the 16-hex hash lands in the fact store.
    """
    return hashlib.sha256(token_str.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Token:
    """A freshly-minted token + its CapabilitySet.

    ``token_str`` is the opaque ``persistence.repl/<base64url>`` string the
    operator captures and ships to the consumer out-of-band. ``token_id``
    is the 16-hex content hash; only the hash lives in the fact store.
    """

    token_str: str
    cap_set: CapabilitySet

    @property
    def token_id(self) -> str:
        return _token_id(self.token_str)


def mint_token(
    *,
    caps: frozenset[Capability],
    expires_at: datetime | None = None,
    label: str = "",
) -> Token:
    """Generate a fresh opaque token + capabilities.

    The token is a ``persistence.repl/<base64url-32-bytes>`` string.
    ``secrets.token_urlsafe(32)`` provides 256 bits of cryptographically
    strong entropy. The ``Token`` carries the raw string for the
    issuance call site to display once + ship out-of-band; downstream
    code only ever sees the 16-hex ``token_id``.
    """
    raw = secrets.token_urlsafe(32)
    token_str = f"persistence.repl/{raw}"
    cap_set = CapabilitySet(caps=caps, expires_at=expires_at, label=label)
    return Token(token_str=token_str, cap_set=cap_set)


# ---------------------------------------------------------------------------
# Fact-store interactions
# ---------------------------------------------------------------------------
def store_token(
    db,
    token: Token,
    *,
    runtime_clock: Callable[[], datetime],
) -> None:
    """Persist a token to the fact store via ``db.transact``.

    Writes one datom: ``(e=token_id, a="repl/cap-set", v=<canonical-JSON>)``.
    ``valid_from`` is ``runtime_clock()``-ISO. Subsequent ``validate_token``
    calls read this entity via ``db.as_of(now).entity(token_id)``.

    The raw ``token.token_str`` is NEVER passed to ``db.transact``; only
    the canonical-JSON of the cap-set + the 16-hex ``token_id`` reach the
    fact store.
    """
    cap_json = canonical_dumps(token.cap_set.to_canonical())
    valid_from = runtime_clock().isoformat()
    db.transact([
        {
            "e": token.token_id,
            "a": "repl/cap-set",
            "v": cap_json,
            "valid_from": valid_from,
        },
    ])


def validate_token(
    db,
    token_str: str,
    *,
    runtime_clock: Callable[[], datetime],
) -> CapabilitySet | None:
    """Validate a presented raw token string.

    Returns the ``CapabilitySet`` on success; ``None`` if any of:

    - the token_id is not in the store (never issued or never reached
      this DB),
    - the entity carries ``repl/revoked == True``,
    - the cap-set's ``expires_at`` has passed (``is_expired(now)``).

    Reads via ``db.as_of(runtime_clock()).entity(token_id)`` — a single
    bitemporal lookup. Latency is bounded by the per-token history
    depth (3-4 datoms in v0.7.0a1: issue + optional revoke).
    """
    tid = _token_id(token_str)
    now = runtime_clock()
    view = db.as_of(now)
    entity = view.entity(tid)
    if not entity:
        return None
    cap_json = entity.get("repl/cap-set")
    if cap_json is None:
        return None
    if entity.get("repl/revoked") is True:
        return None
    cap_dict = json.loads(cap_json)
    cap_set = CapabilitySet.from_canonical_dict(cap_dict)
    if cap_set.is_expired(now):
        return None
    return cap_set


def revoke_token(
    db,
    token_id: str,
    *,
    runtime_clock: Callable[[], datetime],
) -> None:
    """Revoke a token by writing ``repl/revoked = True``. Idempotent.

    Repeat invocations are no-ops at the validation layer — the
    cardinality-one auto-retraction in ``DB.transact`` collapses the
    second write into a retract+assert pair, but the entity projection
    still reads ``repl/revoked == True``. Mid-op semantics (MINOR-3):
    revocation takes effect on the next ``validate_token`` call;
    in-flight ops are not interrupted.
    """
    valid_from = runtime_clock().isoformat()
    db.transact([
        {
            "e": token_id,
            "a": "repl/revoked",
            "v": True,
            "valid_from": valid_from,
        },
    ])


def list_tokens(
    db,
    *,
    runtime_clock: Callable[[], datetime],
) -> list[dict]:
    """List active (non-revoked) tokens in the store.

    Returns one row per active token::

        {
            "token_id": "<16-hex>",
            "label": "<human-readable>",
            "expires_at_iso": "<ISO-8601>" | "never",
            "caps_summary": "inspect:read,edit:write,...",
        }

    Substrate query primitive: the v0.4.0a1 ``DB`` exposes
    ``log()`` (full datom iterator) and ``as_of(now)`` returning a
    ``DBView`` with a per-entity ``entity(e)`` projection but NO
    ``entities_with(attr)`` primitive. We project the set of entity ids
    that have ever held a ``repl/cap-set`` datom by walking the log
    once, then call ``view.entity(eid)`` for each. This is a
    documented O(N_datoms + N_tokens) scan — fine for the v0.7.0a1
    operator-CLI scale; a future ``DB.entities_with(attr)`` primitive
    would cut it to O(N_tokens). Doing this in user-space avoids the
    "no new substrate APIs" §3 scope rule.

    Expired tokens are still listed (their ``expires_at_iso`` is past);
    expiry is a validation concern, not a listing concern. Revoked
    tokens are filtered.
    """
    now = runtime_clock()
    view = db.as_of(now)

    # Walk the log once to find every entity that has ever carried a
    # ``repl/cap-set`` datom. This is the user-space substitute for a
    # missing ``DBView.entities_with(attr)`` primitive — see §3 scope.
    candidate_ids: list[str] = []
    seen: set[str] = set()
    for d in db.log():
        if d.a == "repl/cap-set" and d.e not in seen:
            seen.add(d.e)
            candidate_ids.append(d.e)

    out: list[dict] = []
    for tid in candidate_ids:
        entity = view.entity(tid)
        if not entity:
            continue
        cap_json = entity.get("repl/cap-set")
        if cap_json is None:
            continue
        if entity.get("repl/revoked") is True:
            continue
        cap_dict = json.loads(cap_json)
        cap_set = CapabilitySet.from_canonical_dict(cap_dict)
        caps_summary = ",".join(
            f"{c.op}:{c.qualifier}"
            for c in sorted(cap_set.caps, key=lambda c: (c.op, c.qualifier))
        )
        out.append({
            "token_id": tid,
            "label": cap_set.label,
            "expires_at_iso": (
                cap_set.expires_at.isoformat() if cap_set.expires_at else "never"
            ),
            "caps_summary": caps_summary,
        })
    return out


__all__ = [
    "ALL_CAPS",
    "Capability",
    "CapabilitySet",
    "OP_NAMES",
    "QUALIFIERS_BY_OP",
    "Token",
    "UnknownCapability",
    "_token_id",
    "list_tokens",
    "mint_token",
    "revoke_token",
    "store_token",
    "validate_token",
]
