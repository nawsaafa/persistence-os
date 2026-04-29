"""Synthetic agent-trajectory generator for the regulator-replay benchmark.

For each seed in ``--seeds`` (default ``0-49``) the generator produces

* ``trajectories/<seed>.audit.jsonl`` — the Merkle-chained audit log
  (one :class:`persistence.effect.AuditEntry` per line, JSON-encoded
  via ``entry.to_dict()``).
* ``trajectories/<seed>.log.jsonl`` — the operation record (one row
  per performed op, capturing the args we passed plus the
  intent-tag — ``retraction`` / ``tier_change`` / ``retroactive`` —
  so an auditor can read the trajectory without re-running it).

Determinism contract
--------------------

The generator never calls ``time.time``, ``datetime.now``,
``random.random``, or ``os.urandom`` in the trajectory body. The only
randomness source is :class:`numpy.random.Generator` seeded with
``seed``; the only "clock" is a counter clock handler local to this
module that increments by one per ``:clock/now``. Re-running the
generator with the same seeds therefore produces byte-identical
``.audit.jsonl`` and ``.log.jsonl`` files (this is the property the
:mod:`bench.regulator_replay.harness` relies on, and the property
the :mod:`bench.regulator_replay.tests.test_generator` smoke test
asserts directly).

Shape of one trajectory
-----------------------

A trajectory is ``--length`` mixed operations (default 40) over a
small entity set. The mix is drawn from
``[":vault/remember", ":vault/recall", ":llm/call"]`` with weights
that average out to roughly 45 / 30 / 25 percent. Three positions
are *mandatory* (computed from the seed but bounded into thirds of
the trajectory) and override the random draw:

* a tier-changing ``:vault/remember`` (the entity's ``tier`` slot
  goes from ``L2`` to ``L1`` or vice versa);
* a ``:vault/remember`` with ``op="retract"`` that retracts the
  most recent assert on a chosen entity;
* a retroactive correction (``force_retroactive=True``) — a fresh
  assert whose ``valid_from`` is strictly earlier than the entity's
  prior open assert. The substrate's
  :class:`persistence.fact.RetroactiveCorrectionError` is opted-in.

These three positions are the regulatory-interest hotspots the
benchmark exists to demonstrate replay against. Every operation
goes through a :class:`persistence.effect.Runtime` whose audit
handler wraps all three ops, so each trajectory's
``.audit.jsonl`` is a real Merkle-chained log.

CLI
---

``python -m bench.regulator_replay.generator --seeds 0-49 --length 40``

See ``README.md`` for the manifest format and the auditor's
read-the-corpus walkthrough.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from persistence import __version__ as _persistence_version
from persistence.effect import (
    AuditEntry,
    Handler,
    Runtime,
    make_audit_handler,
    perform,
    with_runtime,
)
from persistence.fact import DB, InMemoryStore

from bench.regulator_replay import CORPUS_VERSION

# Public surface --------------------------------------------------------------

__all__ = [
    "DEFAULT_LENGTH",
    "DEFAULT_SEEDS",
    "VAULT_OPS",
    "generate_trajectory",
    "generate_corpus",
    "build_manifest",
    "main",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_LENGTH = 40
DEFAULT_SEEDS = range(50)

VAULT_OPS: tuple[str, ...] = (":vault/remember", ":vault/recall", ":llm/call")
"""Ops the benchmark exercises. The audit handler wraps all three; the
local raw handler at the bottom of the stack supplies their semantics
without ever touching the network or wall clock.
"""

ENTITY_POOL: tuple[str, ...] = tuple(f"entity:{i}" for i in range(6))
"""Six entities — small enough that retractions and tier changes
hit the same entity multiple times within 40 ops, which is the
shape an auditor expects to see."""

ATTR = ":fact/state"
"""The single attribute we vary per trajectory. Keeping it constant
across all trajectories means the manifest's per-trajectory hash is
purely a function of (seed, length, ops chosen) — the schema is
identical."""


# ---------------------------------------------------------------------------
# Local handlers — counter clock, in-memory vault, deterministic LLM stub
# ---------------------------------------------------------------------------


def _make_counter_clock_handler() -> Handler:
    """A clock that returns a monotonically-increasing integer per call.

    The audit handler reads ``:clock/now`` twice per wrapped op (start
    and finish), so a 40-op trajectory consumes 80 ticks. We start at
    a fixed epoch baseline so ``recorded_at`` projects to a real
    tz-aware datetime via ``_recorded_at_to_inst`` without surprises.

    Lives in this module (not :mod:`persistence.effect.handlers.clock`)
    because the benchmark is self-contained — anything we add here
    must NOT modify the substrate.
    """

    def clock_now(args: dict[str, Any], k, ctx: dict[str, Any]) -> dict[str, float]:  # noqa: ARG001
        ctx["t"] = ctx["t"] + 1.0
        return {"ts": ctx["t"]}

    return Handler(
        name="clock",
        wraps={":clock/now"},
        clauses={":clock/now": clock_now},
        # Baseline: 2026-01-01T00:00:00+00:00 in Unix-epoch seconds, minus
        # one tick so the first :clock/now call lands on the boundary.
        ctx={"t": 1_767_225_600.0 - 1.0},
    )


def _make_vault_handler(db_box: dict[str, DB]) -> Handler:
    """Handler that gives ``:vault/remember`` and ``:vault/recall`` semantics.

    ``db_box`` is a single-slot mutable holder so the handler can swap
    the active :class:`persistence.fact.DB` value (each ``transact``
    returns a new DB; the holder lets the handler see the latest one).

    The handler returns plain dicts only — no live DB references in
    return values — so canonical hashing is deterministic across
    Python versions.
    """

    def remember(args: dict[str, Any], k, ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        db = db_box["db"]
        force = bool(args.get("force_retroactive", False))
        # The remember-args use ``"fact_op"`` (not ``"op"``) to name the
        # assert/retract verb because :func:`persistence.effect.perform`
        # already binds ``op`` as the first positional argument; a
        # collision raises ``TypeError: got multiple values for argument
        # 'op'`` at dispatch time.
        fact_op = args.get("fact_op", "assert")
        entity = args["entity"]
        value = args["value"]
        valid_from_str = args["valid_from"]
        # The DB API expects datetimes; we accept ISO strings on the wire
        # so the audit args_hash is stable across machines.
        valid_from = _dt.datetime.fromisoformat(valid_from_str)

        fact = {
            "e": entity,
            "a": ATTR,
            "v": value,
            "valid_from": valid_from,
            "op": fact_op,
        }
        new_db = db.transact([fact], force_retroactive=force)
        db_box["db"] = new_db
        # Latest tx is the count of distinct tx ids in the log.
        latest_tx = max((d.tx for d in new_db.log()), default=0)
        return {
            "tx": latest_tx,
            "entity": entity,
            "fact_op": fact_op,
        }

    def recall(args: dict[str, Any], k, ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        db = db_box["db"]
        entity = args["entity"]
        # History — projected to a list of (a, v, valid_from-iso, op) tuples
        # so the canonical hash is purely a function of asserted facts and
        # not Datom object identity.
        history = db.history(entity)
        rows = [
            {
                "a": d.a,
                "v": d.v,
                "valid_from": d.valid_from.isoformat(),
                "valid_to": d.valid_to.isoformat() if d.valid_to else None,
                "op": d.op,
                "tx": d.tx,
            }
            for d in history
        ]
        # vault_snapshot_tx — the max tx visible to this query (paper
        # §6.3). Captured here so the harness has the snapshot anchor
        # the design doc promised; we surface it in the .log.jsonl
        # record but do not embed it in the AuditEntry (the substrate
        # AuditEntry schema is frozen at v0.7.0a1 — see the bitemporal
        # design doc's ADR for why we picked this layering).
        vault_snapshot_tx = max((d.tx for d in db.log()), default=0)
        return {
            "entity": entity,
            "rows": rows,
            "vault_snapshot_tx": vault_snapshot_tx,
        }

    def llm_call(args: dict[str, Any], k, ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        messages = args.get("messages", [])
        content = messages[-1].get("content", "") if messages else ""
        # Deterministic body keyed off the seed-tagged content. We hash
        # the input rather than echoing it verbatim so the synthetic
        # corpus does not leak generator-internal opIDs into model
        # output the way ``echo:<...>`` would.
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return {
            "text": f"synth:{digest}",
            "usage": {"tokens": 8 + (len(content) % 7)},
            "fingerprint": "regulator-replay/v1",
        }

    return Handler(
        name="raw-vault",
        wraps=set(VAULT_OPS),
        clauses={
            ":vault/remember": remember,
            ":vault/recall": recall,
            ":llm/call": llm_call,
        },
    )


# ---------------------------------------------------------------------------
# Trajectory body — pure planning of the op sequence
# ---------------------------------------------------------------------------


def _plan_ops(seed: int, length: int) -> list[dict[str, Any]]:
    """Return the sequenced list of operations for one trajectory.

    The planner is pure (numpy RNG + integer math). It never touches
    the DB, the runtime, or any handler. The DB is constructed later
    and these planned ops are executed against it; that separation
    makes the trajectory shape inspectable in isolation
    (``test_generator`` exercises the planner directly).

    Three slots are fixed by the seed-derived layout — exactly one
    tier-change, one retraction, one retroactive correction, all
    landing as ``:vault/remember`` ops at distinct indices so the
    Merkle chain is interesting in the same regions every regulator
    would look.
    """
    rng = np.random.default_rng(seed)

    # Mandatory slot indices — bounded into the three thirds of the
    # trajectory. Distinct by construction.
    third = max(1, length // 3)
    tier_idx = int(2 + (seed * 3) % third)
    retract_idx = int(third + 1 + (seed * 5) % third)
    retroactive_idx = int(2 * third + (seed * 7) % third)
    # Clamp into bounds.
    tier_idx = min(tier_idx, length - 3)
    retract_idx = min(retract_idx, length - 2)
    retroactive_idx = min(retroactive_idx, length - 1)

    # The "valid_from" timeline — base epoch advances 1 day per index.
    # Mandatory retroactive op rewinds 5 days from current marker.
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)

    ops: list[dict[str, Any]] = []
    seeded_remembered_entities: list[str] = []

    for i in range(length):
        # Default valid_from: i days after base. Strictly monotonic so
        # vanilla asserts never trigger RetroactiveCorrectionError.
        valid_from = base + _dt.timedelta(days=i)
        valid_from_iso = valid_from.isoformat()

        if i == tier_idx:
            # Force a tier change on entity 0. We need a prior assert on
            # the same entity with a different tier; the loop seeds one
            # at i=0 below.
            entity = ENTITY_POOL[0]
            # Tier change: L2 → L1 (the seed-0 prior assert is L2).
            value = {"tier": "L1", "topic": "memo", "rev": int(i)}
            ops.append(
                {
                    "kind": ":vault/remember",
                    "intent": "tier_change",
                    "args": {
                        "entity": entity,
                        "value": value,
                        "valid_from": valid_from_iso,
                        "fact_op": "assert",
                    },
                }
            )
            seeded_remembered_entities.append(entity)
            continue

        if i == retract_idx:
            # Retract entity 1's prior assert. Seeded at i=1 below.
            entity = ENTITY_POOL[1]
            value = {"tier": "L2", "topic": "memo", "rev": int(i)}
            ops.append(
                {
                    "kind": ":vault/remember",
                    "intent": "retraction",
                    "args": {
                        "entity": entity,
                        "value": value,
                        "valid_from": valid_from_iso,
                        "fact_op": "retract",
                    },
                }
            )
            continue

        if i == retroactive_idx:
            # Retroactive correction: rewind 5 days. Entity 2 is seeded
            # at i=2, so by retroactive_idx the entity has open asserts
            # whose valid_from is later than the rewound timestamp.
            entity = ENTITY_POOL[2]
            rewound = base + _dt.timedelta(days=max(0, i - 5))
            value = {"tier": "L1", "topic": "wacc", "rev": int(i), "retroactive": True}
            ops.append(
                {
                    "kind": ":vault/remember",
                    "intent": "retroactive",
                    "args": {
                        "entity": entity,
                        "value": value,
                        "valid_from": rewound.isoformat(),
                        "fact_op": "assert",
                        "force_retroactive": True,
                    },
                }
            )
            continue

        if i in (0, 1, 2):
            # Seed asserts for the mandatory-slot entities so the
            # mandatory ops land on something that already exists.
            entity = ENTITY_POOL[i]
            tier = "L2"
            value = {"tier": tier, "topic": "memo", "rev": int(i)}
            ops.append(
                {
                    "kind": ":vault/remember",
                    "intent": "seed",
                    "args": {
                        "entity": entity,
                        "value": value,
                        "valid_from": valid_from_iso,
                        "fact_op": "assert",
                    },
                }
            )
            continue

        # Free slot — sample an op kind by RNG.
        kind = rng.choice(VAULT_OPS, p=[0.45, 0.30, 0.25])
        if kind == ":vault/remember":
            entity = ENTITY_POOL[int(rng.integers(0, len(ENTITY_POOL)))]
            tier = ["L1", "L2", "L3"][int(rng.integers(0, 3))]
            value = {
                "tier": tier,
                "topic": ["memo", "wacc", "summary", "risk"][int(rng.integers(0, 4))],
                "rev": int(i),
            }
            ops.append(
                {
                    "kind": ":vault/remember",
                    "intent": "free",
                    "args": {
                        "entity": entity,
                        "value": value,
                        "valid_from": valid_from_iso,
                        "fact_op": "assert",
                    },
                }
            )
        elif kind == ":vault/recall":
            entity = ENTITY_POOL[int(rng.integers(0, len(ENTITY_POOL)))]
            ops.append(
                {
                    "kind": ":vault/recall",
                    "intent": "free",
                    "args": {"entity": entity},
                }
            )
        else:  # :llm/call
            content = f"seed={seed} op={i} bucket={int(rng.integers(0, 100))}"
            ops.append(
                {
                    "kind": ":llm/call",
                    "intent": "free",
                    "args": {
                        "model": "regulator-replay-stub",
                        "messages": [{"role": "user", "content": content}],
                    },
                }
            )

    # Validate mandatory presence — any drift here means the planner
    # silently dropped a regulator-interest slot, which would break
    # the corpus contract advertised in README.md.
    intents = {o["intent"] for o in ops}
    missing = {"tier_change", "retraction", "retroactive"} - intents
    if missing:
        raise AssertionError(
            f"planner failed to emit mandatory intents {missing!r} "
            f"for seed={seed} length={length}"
        )
    return ops


# ---------------------------------------------------------------------------
# Trajectory body — execution against the substrate
# ---------------------------------------------------------------------------


def _execute(ops: list[dict[str, Any]]) -> tuple[list[AuditEntry], list[dict[str, Any]]]:
    """Run the planned ``ops`` through a Runtime + audit handler.

    Returns the chained :class:`AuditEntry` list and a parallel list
    of operation records (one per op), each capturing the original
    args, the returned value, and the intent tag.
    """
    db_box: dict[str, DB] = {"db": DB(InMemoryStore())}
    audit_entries: list[AuditEntry] = []
    audit = make_audit_handler(audit_entries, wraps=set(VAULT_OPS))
    raw = _make_vault_handler(db_box)
    clock = _make_counter_clock_handler()
    rt = Runtime([raw, clock, audit])

    log_records: list[dict[str, Any]] = []
    with with_runtime(rt):
        for i, op in enumerate(ops):
            kind = op["kind"]
            args = op["args"]
            try:
                result = perform(kind, **args)
                error = None
            except Exception as exc:  # noqa: BLE001
                # The retroactive opt-in path means we do NOT expect
                # exceptions here — the planner sets force_retroactive
                # whenever it walks valid_from backwards. If one slips
                # through, fail loudly so the corpus doesn't ship a
                # half-realised intent.
                raise RuntimeError(
                    f"unexpected exception at op #{i} ({kind}, intent={op['intent']!r}): {exc}"
                ) from exc

            log_records.append(
                {
                    "index": i,
                    "op": kind,
                    "intent": op["intent"],
                    "args": args,
                    "result": result,
                    "error": error,
                }
            )
    return audit_entries, log_records


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _entry_to_jsonl(entry: AuditEntry) -> str:
    """Serialize one :class:`AuditEntry` to a single JSONL line.

    Uses ``entry.to_dict()`` (which already omits the optional
    ``txn_commit`` slot when unset) and JSON-encodes with sorted keys
    so two runs of the generator produce byte-identical output. The
    inverse is :func:`_jsonl_to_entry`.
    """
    d = entry.to_dict()
    # ``handler_chain`` is a tuple — JSON has no tuple, but lists round-
    # trip back to tuples in :func:`_jsonl_to_entry`.
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def _jsonl_to_entry(line: str) -> AuditEntry:
    """Inverse of :func:`_entry_to_jsonl` — reconstruct one entry.

    Coerces ``handler_chain`` back to ``tuple`` so the dataclass
    invariants (and downstream Merkle recomputation) hold.
    """
    d = json.loads(line)
    if "handler_chain" in d and isinstance(d["handler_chain"], list):
        d["handler_chain"] = tuple(d["handler_chain"])
    return AuditEntry(**d)


def load_audit_jsonl(path: Path) -> list[AuditEntry]:
    """Load an audit chain from a ``<seed>.audit.jsonl`` file.

    Used by the harness; exposed at module scope so external auditors
    can inspect a trajectory without re-importing private helpers.
    """
    entries: list[AuditEntry] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            entries.append(_jsonl_to_entry(line))
    return entries


def _records_to_jsonl(records: list[dict[str, Any]]) -> str:
    """Serialize the parallel op-record list to a JSONL blob (string)."""
    out_lines = [
        json.dumps(rec, sort_keys=True, ensure_ascii=False) for rec in records
    ]
    return "\n".join(out_lines) + ("\n" if out_lines else "")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def generate_trajectory(
    seed: int, length: int = DEFAULT_LENGTH
) -> tuple[list[AuditEntry], list[dict[str, Any]]]:
    """Plan and execute one trajectory; return ``(audit_entries, op_records)``.

    Pure function of ``(seed, length)``. Calling it twice in the same
    process — or in two separate processes — yields the same output
    bytes after :func:`_entry_to_jsonl` / :func:`_records_to_jsonl`.
    """
    ops = _plan_ops(seed, length)
    return _execute(ops)


def write_trajectory(
    seed: int,
    out_dir: Path,
    *,
    length: int = DEFAULT_LENGTH,
) -> dict[str, Path]:
    """Generate one trajectory and write its two output files.

    Returns ``{"audit": <path>, "log": <path>}`` for the manifest
    builder to hash without re-reading from arbitrary paths.
    """
    entries, records = generate_trajectory(seed, length)

    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / f"{seed:03d}.audit.jsonl"
    log_path = out_dir / f"{seed:03d}.log.jsonl"

    audit_blob = "\n".join(_entry_to_jsonl(e) for e in entries) + (
        "\n" if entries else ""
    )
    log_blob = _records_to_jsonl(records)

    audit_path.write_text(audit_blob, encoding="utf-8")
    log_path.write_text(log_blob, encoding="utf-8")
    return {"audit": audit_path, "log": log_path}


def generate_corpus(
    seeds: Iterable[int],
    out_dir: Path,
    *,
    length: int = DEFAULT_LENGTH,
) -> list[dict[str, Path]]:
    """Generate all trajectories for ``seeds`` under ``out_dir``.

    Returns the per-trajectory path bundles in seed order so the
    manifest builder can iterate deterministically.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    bundles: list[dict[str, Path]] = []
    for seed in seeds:
        bundles.append(write_trajectory(seed, out_dir, length=length))
    return bundles


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _sha256_of_paths(paths: list[Path]) -> str:
    """SHA-256 of the byte-concatenation of ``paths`` in given order.

    Used both for the per-trajectory hash (audit + log pair) and
    the corpus-root hash (all trajectory files in lexicographic
    order). Reads each file as bytes so the digest is independent of
    Python's text decoding.
    """
    h = hashlib.sha256()
    for p in paths:
        with p.open("rb") as fh:
            h.update(fh.read())
    return "sha256:" + h.hexdigest()


def build_manifest(
    bundles: list[dict[str, Path]],
    *,
    seeds: list[int],
    length: int,
    out_dir: Path,
    invocation: str,
    git_sha: str | None = None,
) -> dict[str, Any]:
    """Build the corpus manifest from already-written trajectory files.

    The manifest is the auditor-facing identity card:

    * ``corpus_root`` — sha256 over all trajectory files in sorted-path
      order. One byte flip anywhere in the corpus changes this hash.
    * ``trajectories`` — per-seed list with audit-path, log-path, and
      a pair-level sha256 (audit bytes followed by log bytes).
    * ``substrate_version`` / ``corpus_version`` — pinned for reproduce-
      ability.
    * ``invocation`` — the exact CLI line that produced this corpus.
    * ``git_sha`` — repo HEAD at generation time (filled by the CLI;
      callable callers may pass ``None``).
    """
    trajectories: list[dict[str, Any]] = []
    for seed, bundle in zip(seeds, bundles, strict=True):
        pair_hash = _sha256_of_paths(
            sorted([bundle["audit"], bundle["log"]], key=lambda p: p.name)
        )
        trajectories.append(
            {
                "seed": seed,
                "audit": bundle["audit"].relative_to(out_dir).as_posix(),
                "log": bundle["log"].relative_to(out_dir).as_posix(),
                "sha256": pair_hash,
            }
        )

    # Corpus root: hash over EVERY trajectory file in sorted-path order
    # (deterministic regardless of seed iteration order).
    all_files = sorted(
        [b["audit"] for b in bundles] + [b["log"] for b in bundles],
        key=lambda p: p.name,
    )
    corpus_root = _sha256_of_paths(all_files)

    return {
        "corpus_version": CORPUS_VERSION,
        "substrate_version": _persistence_version,
        "git_sha": git_sha,
        "seed_range": {"min": min(seeds), "max": max(seeds), "count": len(seeds)},
        "ops_per_trajectory": length,
        "vault_ops": list(VAULT_OPS),
        "corpus_root": corpus_root,
        "trajectories": trajectories,
        "invocation": invocation,
        "license": "CC-BY-4.0",
        "attribution": "persistence-os contributors, 2026",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_seeds(spec: str) -> list[int]:
    """Parse ``"0-49"`` / ``"3"`` / ``"0,2,5-7"`` into a sorted list of ints."""
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(chunk))
    if not out:
        raise ValueError(f"empty seed spec: {spec!r}")
    return sorted(set(out))


def _git_sha(repo_root: Path) -> str | None:
    """Return the current HEAD SHA, or ``None`` if not in a git work tree.

    Pure stdlib — we shell out to ``git`` only if it's on PATH; the
    benchmark must remain runnable in a tarball.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.regulator_replay.generator",
        description="Generate the regulator-replay synthetic corpus.",
    )
    parser.add_argument(
        "--seeds",
        default="0-49",
        help="Seed spec (default '0-49'). Accepts '0-49', '3,7', or '0,2,5-7'.",
    )
    parser.add_argument(
        "--length",
        type=int,
        default=DEFAULT_LENGTH,
        help=f"Operations per trajectory (default {DEFAULT_LENGTH}).",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "trajectories"),
        help="Output directory for trajectory files.",
    )
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).parent / "MANIFEST.json"),
        help="Path to the manifest JSON file.",
    )
    args = parser.parse_args(argv)

    seeds = _parse_seeds(args.seeds)
    out_dir = Path(args.out).resolve()
    manifest_path = Path(args.manifest).resolve()

    invocation = "python -m bench.regulator_replay.generator " + " ".join(
        [
            f"--seeds {args.seeds}",
            f"--length {args.length}",
        ]
    )

    bundles = generate_corpus(seeds, out_dir, length=args.length)

    repo_root = Path(__file__).resolve().parents[2]
    manifest = build_manifest(
        bundles,
        seeds=seeds,
        length=args.length,
        out_dir=out_dir,
        invocation=invocation,
        git_sha=_git_sha(repo_root),
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"wrote {len(bundles)} trajectories to {out_dir}; "
        f"manifest at {manifest_path}; corpus_root={manifest['corpus_root']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
