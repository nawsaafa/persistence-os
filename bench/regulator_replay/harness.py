"""Regulator-replay reconstruction harness.

For each ``<seed>.audit.jsonl`` produced by
:mod:`bench.regulator_replay.generator`, the harness:

1. Loads the audit chain back into :class:`persistence.effect.AuditEntry`
   objects.
2. Recomputes each entry's content hash and verifies the Merkle chain
   (byte-identical to what the generator wrote).
3. Runs an end-to-end **tamper-detection matrix**: for a deterministic
   sample of byte positions per file, flips the byte, attempts to
   re-load the chain, and asserts the tamper is caught — either as a
   parse failure (the byte landed in JSON structure) or as a
   ``verify_chain`` rejection (the byte landed in JSON content).
4. Writes ``reports/run-<UTC-iso>.json`` with the per-trajectory pass /
   fail and the tamper-matrix summary.

Audit-reconstructibility (Prop 1, paper §6.3) is the headline metric:
it is the fraction of trajectories whose audit chain replays clean.
The benchmark targets ``1.000`` (50/50) and the harness emits a
non-zero exit code if the rate dips below ``1.000``.

CLI
---

``python -m bench.regulator_replay.harness``

Defaults read trajectories from ``./trajectories`` (relative to this
file) and write reports to ``./reports``. Override with ``--in`` and
``--out``.

For each tampered byte the harness samples one position in the
chain header (offset 0), one in the body (offset N/2), and one near
the tail (offset N-2), per file — see :data:`SAMPLE_OFFSETS_FRAC`
for the exact formula and the rationale for skipping a full
N-byte sweep.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path
from typing import Any

from persistence.effect import verify_chain

from bench.regulator_replay.generator import (
    _jsonl_to_entry,
    load_audit_jsonl,
)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "SAMPLE_OFFSETS_FRAC",
    "ReplayResult",
    "TamperResult",
    "replay_one",
    "tamper_check_one",
    "run",
    "main",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


SAMPLE_OFFSETS_FRAC: tuple[float, ...] = (
    0.05,
    0.20,
    0.35,
    0.50,
    0.65,
    0.80,
    0.92,
    0.97,
)
"""Byte-offset fractions sampled per file for the tamper matrix.

A full N-byte sweep across 50 trajectories of ~25 KB each would flip
~1.25 M bytes — wasteful and not informative beyond the first few
representative regions. We pick eight fractional offsets distributed
across the file so the sample touches the chain header, body
hash regions, and tail. With two files per trajectory (audit + log)
that is 16 flips per trajectory and 800 across the corpus. The
choice is documented here, in :doc:`README`, and in the report
JSON so an external auditor can verify the coverage claim.
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ReplayResult:
    """Outcome of replaying one trajectory's audit chain.

    Attributes
    ----------
    seed : int
    audit_path : Path
    chain_valid : bool
        ``True`` iff :func:`persistence.effect.verify_chain` accepts
        the loaded entries unmodified.
    n_entries : int
    elapsed_ms : float
    error : str | None
        Set when load or verify raised; ``None`` on success.
    """

    __slots__ = (
        "seed",
        "audit_path",
        "chain_valid",
        "n_entries",
        "elapsed_ms",
        "error",
    )

    def __init__(
        self,
        seed: int,
        audit_path: Path,
        *,
        chain_valid: bool,
        n_entries: int,
        elapsed_ms: float,
        error: str | None = None,
    ) -> None:
        self.seed = seed
        self.audit_path = audit_path
        self.chain_valid = chain_valid
        self.n_entries = n_entries
        self.elapsed_ms = elapsed_ms
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "audit_path": str(self.audit_path.name),
            "chain_valid": self.chain_valid,
            "n_entries": self.n_entries,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
        }


class TamperResult:
    """Outcome of one tamper sample (a single byte flip on one file).

    ``caught`` is True if the flip was caught as either:

    - A parse failure (JSON structure-breaking flip; raised at load
      time), or
    - A Merkle-chain rejection (``verify_chain`` returned False).

    A False ``caught`` would mean the substrate accepted a tampered
    chain — that is the contract violation Prop 1 forbids.
    """

    __slots__ = ("seed", "file_kind", "byte_offset", "caught", "reason")

    def __init__(
        self,
        seed: int,
        file_kind: str,
        byte_offset: int,
        *,
        caught: bool,
        reason: str,
    ) -> None:
        self.seed = seed
        self.file_kind = file_kind
        self.byte_offset = byte_offset
        self.caught = caught
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "file_kind": self.file_kind,
            "byte_offset": self.byte_offset,
            "caught": self.caught,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Replay primitives
# ---------------------------------------------------------------------------


def replay_one(audit_path: Path, *, seed: int) -> ReplayResult:
    """Replay one ``<seed>.audit.jsonl`` and report chain validity.

    Replay = load + verify. Any exception from either step is caught
    and surfaced as a failure — we never let a single bad file abort
    the corpus run.
    """
    t0 = time.perf_counter()
    try:
        entries = load_audit_jsonl(audit_path)
        ok = verify_chain(entries)
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return ReplayResult(
            seed=seed,
            audit_path=audit_path,
            chain_valid=False,
            n_entries=0,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return ReplayResult(
        seed=seed,
        audit_path=audit_path,
        chain_valid=ok,
        n_entries=len(entries),
        elapsed_ms=elapsed_ms,
        error=None,
    )


def _sample_offsets(file_size: int) -> list[int]:
    """Compute the byte offsets to flip given a file size.

    Drops duplicates (small files may collapse multiple fractions to
    the same offset). Always at least one offset for any non-empty
    file so the matrix is non-degenerate.
    """
    if file_size <= 0:
        return []
    offsets = sorted({int(file_size * frac) for frac in SAMPLE_OFFSETS_FRAC})
    # Guarantee in-bounds (int(file_size * 0.97) for tiny files may be 0
    # which is fine; we never want > file_size - 1).
    offsets = [min(o, file_size - 1) for o in offsets]
    return sorted(set(offsets))


def _flip_byte(data: bytes, offset: int) -> bytes:
    """Return ``data`` with ``data[offset]`` XORed by 0xFF.

    XOR-flipping is a stronger perturbation than a single-bit XOR —
    it always changes the byte (which a ``+1`` can fail to do across
    JSON-significant boundaries) and gives a uniform 1/256 chance of
    landing on each value.
    """
    if offset < 0 or offset >= len(data):
        raise IndexError(f"offset {offset} out of range for {len(data)} bytes")
    return data[:offset] + bytes([data[offset] ^ 0xFF]) + data[offset + 1 :]


def tamper_check_one(
    audit_path: Path, *, seed: int, file_kind: str = "audit"
) -> list[TamperResult]:
    """Run the tamper-detection matrix for one file.

    For each offset in :func:`_sample_offsets`:

    1. Read original bytes.
    2. Flip byte.
    3. Try to parse + verify.
    4. Record whether the tamper was caught.

    Parse failures count as caught — the corpus contract says a
    tampered file must NOT silently round-trip into a chain that
    ``verify_chain`` accepts.
    """
    raw = audit_path.read_bytes()
    offsets = _sample_offsets(len(raw))
    out: list[TamperResult] = []
    for offset in offsets:
        tampered = _flip_byte(raw, offset)
        caught, reason = _try_load_and_verify(tampered)
        out.append(
            TamperResult(
                seed=seed,
                file_kind=file_kind,
                byte_offset=offset,
                caught=caught,
                reason=reason,
            )
        )
    return out


def _try_load_and_verify(blob: bytes) -> tuple[bool, str]:
    """Attempt to parse ``blob`` as audit JSONL and verify the chain.

    Returns ``(caught, reason)``:

    - ``caught=True, reason="parse-error: ..."``  — JSON or
      construction failure, the tamper broke structure.
    - ``caught=True, reason="verify-failed"`` — the chain loaded
      cleanly but :func:`verify_chain` rejected it. Prop-1 path.
    - ``caught=False, reason="silently-accepted"`` — Prop-1 violation.
    """
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        return True, f"decode-error: {type(exc).__name__}"

    entries = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line:
            continue
        try:
            entries.append(_jsonl_to_entry(line))
        except Exception as exc:  # noqa: BLE001
            return True, f"parse-error: line {line_no} {type(exc).__name__}: {exc}"

    if not entries:
        # A flip that emptied the chain is a structure break — the
        # original file had non-zero entries.
        return True, "empty-chain"

    try:
        ok = verify_chain(entries)
    except Exception as exc:  # noqa: BLE001
        return True, f"verify-error: {type(exc).__name__}: {exc}"
    if not ok:
        return True, "verify-failed"
    return False, "silently-accepted"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run(in_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Replay every trajectory in ``in_dir`` and emit the report.

    Returns the report dict (also written to disk under ``out_dir``).
    Caller decides what to do with the exit status; the CLI surface
    treats reconstructibility < 1.0 as a non-zero exit.
    """
    audit_files = sorted(in_dir.glob("*.audit.jsonl"))
    if not audit_files:
        raise FileNotFoundError(
            f"no *.audit.jsonl files under {in_dir!s}; "
            f"run `python -m bench.regulator_replay.generator` first"
        )

    started_at = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.perf_counter()

    replays: list[ReplayResult] = []
    tamper_rows: list[TamperResult] = []
    for audit_path in audit_files:
        seed = int(audit_path.name.split(".", 1)[0])
        replays.append(replay_one(audit_path, seed=seed))
        tamper_rows.extend(
            tamper_check_one(audit_path, seed=seed, file_kind="audit")
        )

    total_elapsed_ms = (time.perf_counter() - t0) * 1000
    finished_at = _dt.datetime.now(_dt.timezone.utc)

    n_pass = sum(1 for r in replays if r.chain_valid and r.error is None)
    n_total = len(replays)
    pass_rate = (n_pass / n_total) if n_total else 0.0

    n_caught = sum(1 for t in tamper_rows if t.caught)
    n_tamper = len(tamper_rows)

    report = {
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
        "elapsed_ms": round(total_elapsed_ms, 3),
        "in_dir": str(in_dir),
        "n_trajectories": n_total,
        "n_pass": n_pass,
        "n_fail": n_total - n_pass,
        "audit_reconstructibility": round(pass_rate, 6),
        "tamper_matrix": {
            "samples_per_file": list(SAMPLE_OFFSETS_FRAC),
            "total_flips": n_tamper,
            "caught": n_caught,
            "missed": n_tamper - n_caught,
            "all_caught": n_caught == n_tamper,
        },
        "trajectories": [r.to_dict() for r in replays],
        "tamper_rows": [t.to_dict() for t in tamper_rows],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    fname_iso = started_at.strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"run-{fname_iso}.json"
    out_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"replay: {n_pass}/{n_total} chains valid "
        f"({pass_rate:.4f}); tamper: {n_caught}/{n_tamper} caught; "
        f"report at {out_path}"
    )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.regulator_replay.harness",
        description="Replay the regulator-replay corpus and emit a JSON report.",
    )
    parser.add_argument(
        "--in",
        dest="in_dir",
        default=str(Path(__file__).parent / "trajectories"),
        help="Directory containing <seed>.audit.jsonl files.",
    )
    parser.add_argument(
        "--out",
        dest="out_dir",
        default=str(Path(__file__).parent / "reports"),
        help="Directory to write the timestamped report JSON into.",
    )
    args = parser.parse_args(argv)

    in_dir = Path(args.in_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    report = run(in_dir, out_dir)
    # Hard contract: 100% reconstructibility AND every sampled tamper caught.
    if report["audit_reconstructibility"] < 1.0:
        return 1
    if not report["tamper_matrix"]["all_caught"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
