"""PG4 cross-process writer harness — multiprocessing.Barrier shape.

Per the design doc ``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md``
§11 G3e (W4-rewritten) and the PG4 task scope (#167), this module ships
the multiprocessing primitive used by
:mod:`tests.store.test_g3e_falsifiability` to spawn two concurrent
writers whose read-sets are captured BEFORE either commits — the
load-bearing synchronisation that keeps the SSI 2-cycle reachable
under READ COMMITTED.

Why a barrier
-------------

R5 SHOULD-FIX (carry-forward, see §13 ARIS R5 verification round) flagged
that without an explicit ``multiprocessing.Barrier`` the
``tx_allocator FOR UPDATE`` row-lock inside ``transact_serializable``
serialises writers — only one is ever inside its own SELECT-then-INSERT
window at a time, so the SSI rw-cycle anomaly never materialises and
G3e becomes vacuous. The barrier moves the synchronisation point UP one
level: each writer reads its read-set at the application layer (a plain
``SELECT v FROM datom_log WHERE e=%s AND a=%s``), waits at the barrier
until BOTH writers have captured their read-set, then runs its INSERT
+ COMMIT. The two transactions therefore truly overlap: each holds a
snapshot taken before the other's writes, which is the precondition
for SSI to find a rw-anti-dependency cycle.

Spawn vs fork
-------------

Per ADR-R10 W1-NEW + § 14 R8, we use ``multiprocessing.get_context('spawn')``
(NOT fork). psycopg connections are NOT fork-safe — a forked child
inherits the parent's TCP socket FDs and any in-flight psycopg state,
which leads to corrupted protocol exchange on the very first cursor.
Spawn is slower but deterministic across macOS + Linux.

Skip rule
---------

This module is consumed by tests that already gate on
``PERSISTENCE_PG_DSN``; the helpers here do not gate themselves so a
caller in a no-DSN environment can still import the module without
side effects. Importing the module never opens a connection.
"""
from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from datetime import datetime, timezone
from multiprocessing.synchronize import Barrier as _BarrierT
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Result dataclass — what each writer process reports back to the parent.
# ---------------------------------------------------------------------------
@dataclass
class CommitResult:
    """Outcome of one writer process.

    Fields are deliberately serialisable — the parent receives this
    over a multiprocessing ``Queue``, so anything inside must be a
    plain dataclass / primitive / repr-string. We do NOT round-trip
    live exceptions or psycopg objects across the process boundary
    because their cross-process serialisation support is uneven;
    instead, ``exception_repr`` holds ``repr(exc)`` and
    ``exception_sqlstate`` holds the SQLSTATE code (or ``None`` if the
    exception was not a psycopg ``Error``).

    Attributes:
        outcome: terminal state of the writer process.
            ``"committed"``: the COMMIT succeeded.
            ``"serialization_failure"``: COMMIT failed with SQLSTATE
                40001 (the SSI rejection path).
            ``"unique_violation"``: COMMIT failed with SQLSTATE 23505
                (defence-in-depth — should not happen for the G3e
                shape but we capture it explicitly for diagnostic
                clarity rather than bucketing into ``other_error``).
            ``"other_error"``: any other exception path. Inspect
                ``exception_repr`` + ``exception_sqlstate`` to
                diagnose.
        worker_id: ``"A"`` or ``"B"`` — identifies which writer.
        read_x: the value of ``X`` the writer observed before the
            barrier (``None`` if the SELECT never ran).
        read_y: the value of ``Y`` the writer observed before the
            barrier (``None`` if the SELECT never ran).
        wrote_e: the entity the writer attempted to update (``"X"``
            or ``"Y"``).
        wrote_v: the value the writer attempted to write (``None`` if
            never reached the INSERT).
        tx_id: the tx-id allocated to this writer's INSERT (``None``
            if never allocated). For the raw-INSERT path we hand-pick
            tx-ids via ``reserve_two_tx_ids`` to avoid contending on
            the ``tx_allocator`` row lock.
        exception_repr: ``repr()`` of the exception that caused a
            non-committed outcome (``None`` for ``"committed"``).
        exception_sqlstate: SQLSTATE code (e.g. ``"40001"``) or
            ``None``.
    """
    outcome: Literal[
        "committed", "serialization_failure", "unique_violation", "other_error"
    ]
    worker_id: str
    read_x: Any = None
    read_y: Any = None
    wrote_e: str | None = None
    wrote_v: Any = None
    tx_id: int | None = None
    exception_repr: str | None = None
    exception_sqlstate: str | None = None


# ---------------------------------------------------------------------------
# Writer-mode literal — exposed so callers can pin the isolation level.
# ---------------------------------------------------------------------------
IsolationLevel = Literal["serializable", "read_committed"]


# ---------------------------------------------------------------------------
# In-process worker entry points.
# ---------------------------------------------------------------------------
def _writer_main_raw(
    *,
    dsn: str,
    isolation: IsolationLevel,
    barrier: _BarrierT,
    queue: Any,
    worker_id: str,
    pre_alloc_tx: int,
    write_target: str,
    decrement: int,
    use_audit: bool,
) -> None:
    """Worker process body — the raw (non-``transact_serializable``) path.

    This is the falsifiability-shape writer: opens a psycopg connection
    directly so we can pin ``isolation_level`` per-call to either
    SERIALIZABLE or READ COMMITTED. Inside one transaction:

    1. ``SELECT v FROM datom_log WHERE e='X' AND a='balance' ORDER BY
       seq DESC LIMIT 1`` — read the current X balance.
    2. Same for ``e='Y'``.
    3. Wait at the parent-supplied ``Barrier`` — this is the load-
       bearing step. Both workers MUST get past their reads before
       either commits, otherwise the ``tx_allocator FOR UPDATE`` row-
       lock would have serialised them and the SSI 2-cycle anomaly
       would not be reachable.
    4. Compute the target value: A writes to X with ``current_y -
       decrement``, B writes to Y with ``current_x - decrement``. This
       is the classic write-skew shape from Cahill, Roehm, Fekete
       (2008): each writer's write depends on the other writer's
       read-set.
    5. INSERT one datom into ``datom_log`` carrying the new value, at
       the pre-allocated ``pre_alloc_tx``. We hand-pick the tx-id via
       ``pre_alloc_tx`` (passed in by the parent before spawn) so the
       two writers do NOT contend on ``tx_allocator FOR UPDATE`` —
       that would re-serialise them and defeat the test.
    6. COMMIT.

    The result is reported back over ``queue``.

    Args:
        dsn: libpq DSN (already includes the per-test ``search_path``).
        isolation: ``"serializable"`` or ``"read_committed"``.
        barrier: shared ``multiprocessing.Barrier(parties=2)``.
        queue: ``multiprocessing.Queue`` for reporting back.
        worker_id: ``"A"`` or ``"B"``.
        pre_alloc_tx: tx-id to stamp on this writer's INSERT.
        write_target: ``"X"`` or ``"Y"`` — which entity to update.
        decrement: how much to subtract from the read of the OTHER
            entity to compute this writer's new value.
        use_audit: when ``True``, the writer additionally INSERTs an
            audit datom (``a='audit/g3e.test'``) after its main write,
            and updates ``audit_chain_lock`` — exercises the cross-
            process audit-chain path even though we are bypassing
            ``transact_serializable`` (the lock-row contention is
            still meaningful).
    """
    # Lazy psycopg imports — keep module-level import safe even when
    # [postgres] extra is not installed in the parent process.
    import psycopg
    from psycopg import errors as pg_errors

    # ----- Connect with the requested isolation level. -------------------
    iso_map = {
        "serializable": psycopg.IsolationLevel.SERIALIZABLE,
        "read_committed": psycopg.IsolationLevel.READ_COMMITTED,
    }

    conn = psycopg.connect(dsn)
    try:
        conn.autocommit = False
        conn.isolation_level = iso_map[isolation]

        cur = conn.cursor()

        # ----- Step 1+2: Read X and Y under our snapshot. ----------------
        # The SELECT establishes the SSI snapshot the writer is going
        # to use; under SERIALIZABLE the predicate locks live on the
        # rows we touch here.
        cur.execute(
            "SELECT v FROM datom_log WHERE e = %s AND a = %s "
            "ORDER BY seq DESC LIMIT 1",
            ("X", "balance"),
        )
        row = cur.fetchone()
        # ``v`` is canonical-JSON TEXT per ADR-5 W1-revised, so read
        # back as int via JSON parse. For our test workload v is
        # always a plain integer encoded as e.g. ``"100"``.
        import json as _json
        read_x = _json.loads(row[0]) if row and row[0] is not None else None

        cur.execute(
            "SELECT v FROM datom_log WHERE e = %s AND a = %s "
            "ORDER BY seq DESC LIMIT 1",
            ("Y", "balance"),
        )
        row = cur.fetchone()
        read_y = _json.loads(row[0]) if row and row[0] is not None else None

        # ----- Step 3: Sync at the barrier — both writers have read. -----
        # If the barrier times out (parent-side hang), we surface as
        # other_error rather than blocking forever.
        try:
            barrier.wait(timeout=30.0)
        except Exception as exc:
            queue.put(
                CommitResult(
                    outcome="other_error",
                    worker_id=worker_id,
                    read_x=read_x,
                    read_y=read_y,
                    exception_repr=f"barrier-timeout: {exc!r}",
                )
            )
            conn.rollback()
            return

        # ----- Step 4: Compute the write. --------------------------------
        # Write-skew shape: A writes X = read_y - decrement; B writes
        # Y = read_x - decrement. Each writer's WRITE depends on the
        # OTHER writer's READ-set entity, but operates on disjoint
        # WRITE entities — that's precisely the anti-dependency
        # pattern SSI is supposed to catch and READ COMMITTED is
        # supposed to MISS.
        if write_target == "X":
            other = read_y if read_y is not None else 0
        else:
            other = read_x if read_x is not None else 0
        new_v = other - decrement

        # ----- Step 5: INSERT — we hand-pick tx-id so the allocator ------
        # row lock does NOT re-serialise the two writers. Each writer
        # gets a distinct ``pre_alloc_tx`` from the parent; both bump
        # ``tx_allocator.next_tx`` from outside (see harness). The
        # UNIQUE (tx, e, a) constraint trivially holds because the
        # two writers target distinct entities.
        now = datetime.now(timezone.utc)
        new_v_text = _json.dumps(new_v, separators=(",", ":"), sort_keys=True)
        prov_text = _json.dumps(
            {"source": "pg4-g3e-test", "writer": worker_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            cur.execute(
                """
                INSERT INTO datom_log
                  (e, a, v, tx, tx_time, valid_from, valid_to,
                   op, provenance, invalidated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    write_target, "balance", new_v_text,
                    pre_alloc_tx, now, now, None,
                    "assert", prov_text, None,
                ),
            )
        except pg_errors.SerializationFailure as exc:
            # SSI can fire on the SELECT (read-only) path on some
            # PG versions — surface immediately.
            queue.put(
                CommitResult(
                    outcome="serialization_failure",
                    worker_id=worker_id,
                    read_x=read_x, read_y=read_y,
                    wrote_e=write_target, wrote_v=new_v,
                    tx_id=pre_alloc_tx,
                    exception_repr=repr(exc),
                    exception_sqlstate=getattr(exc.diag, "sqlstate", None),
                )
            )
            conn.rollback()
            return

        # ----- Optional audit datom -- exercises audit_chain_lock. -------
        # We do this BEFORE COMMIT so the same SERIALIZABLE
        # transaction sees both writes; the lock-row UPDATE is what
        # would conflict cross-process if both writers emit audit.
        if use_audit:
            try:
                cur.execute(
                    "SELECT last_seq, last_hash FROM audit_chain_lock "
                    "WHERE id = 1 FOR UPDATE"
                )
                head_row = cur.fetchone()
                prev_hash = (
                    head_row[1] if head_row and head_row[1] else None
                )
                # Audit datom: a='audit/g3e.test', signature
                # deterministic per worker for chain-continuity check.
                signature = f"sig-{worker_id}-{pre_alloc_tx}"
                audit_v_text = _json.dumps(
                    {"verdict": "ok", "writer": worker_id},
                    separators=(",", ":"), sort_keys=True,
                )
                audit_prov_text = _json.dumps(
                    {
                        ":source": ":pg4-g3e-test",
                        ":signature": signature,
                        ":prev-hash": prev_hash,
                        "parent_provenance_hash": prev_hash,
                    },
                    separators=(",", ":"), sort_keys=True,
                )
                cur.execute(
                    """
                    INSERT INTO datom_log
                      (e, a, v, tx, tx_time, valid_from, valid_to,
                       op, provenance, invalidated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f"audit-{worker_id}-{pre_alloc_tx}",
                        "audit/g3e.test", audit_v_text,
                        pre_alloc_tx, now, now, None,
                        "assert", audit_prov_text, None,
                    ),
                )
                # Update the chain-head pointer.
                cur.execute(
                    "UPDATE audit_chain_lock "
                    "SET last_seq = (SELECT MAX(seq) FROM datom_log "
                    "                WHERE tx = %s), last_hash = %s "
                    "WHERE id = 1",
                    (pre_alloc_tx, signature),
                )
            except pg_errors.SerializationFailure as exc:
                queue.put(
                    CommitResult(
                        outcome="serialization_failure",
                        worker_id=worker_id,
                        read_x=read_x, read_y=read_y,
                        wrote_e=write_target, wrote_v=new_v,
                        tx_id=pre_alloc_tx,
                        exception_repr=repr(exc),
                        exception_sqlstate=getattr(
                            exc.diag, "sqlstate", None
                        ),
                    )
                )
                conn.rollback()
                return

        # ----- Step 6: COMMIT. -------------------------------------------
        try:
            conn.commit()
        except pg_errors.SerializationFailure as exc:
            queue.put(
                CommitResult(
                    outcome="serialization_failure",
                    worker_id=worker_id,
                    read_x=read_x, read_y=read_y,
                    wrote_e=write_target, wrote_v=new_v,
                    tx_id=pre_alloc_tx,
                    exception_repr=repr(exc),
                    exception_sqlstate=getattr(exc.diag, "sqlstate", None),
                )
            )
            return
        except pg_errors.UniqueViolation as exc:
            queue.put(
                CommitResult(
                    outcome="unique_violation",
                    worker_id=worker_id,
                    read_x=read_x, read_y=read_y,
                    wrote_e=write_target, wrote_v=new_v,
                    tx_id=pre_alloc_tx,
                    exception_repr=repr(exc),
                    exception_sqlstate=getattr(exc.diag, "sqlstate", None),
                )
            )
            return

        queue.put(
            CommitResult(
                outcome="committed",
                worker_id=worker_id,
                read_x=read_x, read_y=read_y,
                wrote_e=write_target, wrote_v=new_v,
                tx_id=pre_alloc_tx,
            )
        )
    except BaseException as exc:  # last-ditch — surface on the queue.
        try:
            conn.rollback()
        except Exception:
            pass
        sqlstate = None
        diag = getattr(exc, "diag", None)
        if diag is not None:
            sqlstate = getattr(diag, "sqlstate", None)
        queue.put(
            CommitResult(
                outcome="other_error",
                worker_id=worker_id,
                exception_repr=repr(exc),
                exception_sqlstate=sqlstate,
            )
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


@dataclass
class WriterSpec:
    """Per-writer parameters for :func:`spawn_two_writers`.

    Attributes:
        worker_id: ``"A"`` or ``"B"``.
        pre_alloc_tx: tx-id the writer should stamp on its INSERT.
        write_target: ``"X"`` or ``"Y"`` — entity to UPDATE.
        decrement: subtracted from the OTHER read-set entity to
            compute the new value.
        use_audit: emit an audit datom + bump ``audit_chain_lock``.
    """
    worker_id: str
    pre_alloc_tx: int
    write_target: str
    decrement: int
    use_audit: bool = False


def spawn_two_writers(
    *,
    dsn: str,
    isolation: IsolationLevel,
    spec_a: WriterSpec,
    spec_b: WriterSpec,
    timeout_s: float = 60.0,
) -> tuple[CommitResult, CommitResult]:
    """Spawn two writer processes and return their ``CommitResult``s.

    Both processes synchronise on a shared ``multiprocessing.Barrier``
    AFTER they have captured their read-sets but BEFORE they commit.
    Returns a ``(result_A, result_B)`` tuple in the order matching
    ``spec_a`` / ``spec_b``.

    The function uses ``multiprocessing.get_context('spawn')``
    explicitly so:

    - macOS doesn't fall back to fork (which is unsafe with psycopg).
    - The child does not inherit any open psycopg connection from the
      parent (each child opens fresh).

    Args:
        dsn: libpq DSN — should already include the per-test
            ``search_path`` setting.
        isolation: ``"serializable"`` (the production posture) or
            ``"read_committed"`` (the falsification scenario).
        spec_a: parameters for writer A.
        spec_b: parameters for writer B.
        timeout_s: hard wall-clock cap on ``Process.join`` per
            writer. If a child hangs (typically barrier deadlock or
            connection timeout) the parent terminates and surfaces an
            ``"other_error"`` outcome.

    Returns:
        ``(CommitResult, CommitResult)`` in (A, B) order.
    """
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(parties=2)
    queue = ctx.Queue(maxsize=4)

    procs: list[multiprocessing.Process] = []
    for spec in (spec_a, spec_b):
        p = ctx.Process(
            target=_writer_main_raw,
            kwargs={
                "dsn": dsn,
                "isolation": isolation,
                "barrier": barrier,
                "queue": queue,
                "worker_id": spec.worker_id,
                "pre_alloc_tx": spec.pre_alloc_tx,
                "write_target": spec.write_target,
                "decrement": spec.decrement,
                "use_audit": spec.use_audit,
            },
            daemon=False,
        )
        p.start()
        procs.append(p)

    # Drain results — order-independent, then we re-key by worker_id.
    raw_results: list[CommitResult] = []
    for p in procs:
        p.join(timeout=timeout_s)
        if p.is_alive():
            p.terminate()
            p.join(timeout=5.0)
            raw_results.append(
                CommitResult(
                    outcome="other_error",
                    worker_id="?",
                    exception_repr=f"writer process timed out after {timeout_s}s",
                )
            )

    # Pull whatever the writers reported. We expect at most 2 items;
    # any additional items would indicate a writer reported twice
    # (defensive — never observed in PG4 design).
    while not queue.empty():
        try:
            raw_results.append(queue.get_nowait())
        except Exception:
            break

    by_id: dict[str, CommitResult] = {}
    for r in raw_results:
        if r.worker_id in ("A", "B"):
            by_id[r.worker_id] = r

    a_result = by_id.get("A") or CommitResult(
        outcome="other_error",
        worker_id="A",
        exception_repr="writer A produced no result",
    )
    b_result = by_id.get("B") or CommitResult(
        outcome="other_error",
        worker_id="B",
        exception_repr="writer B produced no result",
    )
    return a_result, b_result


# ---------------------------------------------------------------------------
# Setup helpers — seed the schema with X+Y balances and pre-allocate tx-ids.
# ---------------------------------------------------------------------------
def seed_xy_balances(
    *,
    dsn: str,
    initial_x: int,
    initial_y: int,
) -> int:
    """Seed the per-test schema with two ``balance`` datoms for X + Y.

    Returns the tx-id used (callers reserve subsequent ids via
    :func:`reserve_two_tx_ids`).
    """
    import psycopg
    import json as _json

    conn = psycopg.connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Take the allocator's current tx, bump it, then INSERT
            # both balances at that tx. One tx is sufficient since
            # they are disjoint (e, a) keys.
            cur.execute(
                "SELECT next_tx FROM tx_allocator WHERE id = 1 FOR UPDATE"
            )
            row = cur.fetchone()
            assert row is not None, "tx_allocator must be seeded"
            seed_tx = int(row[0])
            cur.execute(
                "UPDATE tx_allocator SET next_tx = next_tx + 1 WHERE id = 1"
            )
            now = datetime.now(timezone.utc)
            for e, v in (("X", initial_x), ("Y", initial_y)):
                v_text = _json.dumps(v, separators=(",", ":"), sort_keys=True)
                prov_text = _json.dumps(
                    {"source": "pg4-g3e-seed"},
                    separators=(",", ":"), sort_keys=True,
                )
                cur.execute(
                    """
                    INSERT INTO datom_log
                      (e, a, v, tx, tx_time, valid_from, valid_to,
                       op, provenance, invalidated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        e, "balance", v_text,
                        seed_tx, now, now, None,
                        "assert", prov_text, None,
                    ),
                )
        conn.commit()
        return seed_tx
    finally:
        conn.close()


def reserve_two_tx_ids(*, dsn: str) -> tuple[int, int]:
    """Reserve two consecutive tx-ids from ``tx_allocator`` for the writers.

    By advancing the allocator BEFORE we spawn the workers, each
    worker gets a pre-assigned tx-id and avoids contending on the
    ``tx_allocator FOR UPDATE`` row lock — which would re-serialise
    them and defeat the falsifiability proof. Returns ``(tx_a, tx_b)``
    with ``tx_b == tx_a + 1``.
    """
    import psycopg

    conn = psycopg.connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT next_tx FROM tx_allocator WHERE id = 1 FOR UPDATE"
            )
            row = cur.fetchone()
            assert row is not None, "tx_allocator must be seeded"
            tx_a = int(row[0])
            tx_b = tx_a + 1
            cur.execute(
                "UPDATE tx_allocator SET next_tx = next_tx + 2 WHERE id = 1"
            )
        conn.commit()
        return tx_a, tx_b
    finally:
        conn.close()


def read_final_xy(*, dsn: str) -> tuple[Any, Any]:
    """Read the latest ``X`` and ``Y`` balances from the persisted log.

    Latest = highest ``seq`` per ``(e, a)``. Returns ``(x, y)``; either
    may be ``None`` if no datom exists for that entity.
    """
    import psycopg
    import json as _json

    conn = psycopg.connect(dsn)
    try:
        conn.autocommit = True
        out: dict[str, Any] = {}
        with conn.cursor() as cur:
            for e in ("X", "Y"):
                cur.execute(
                    "SELECT v FROM datom_log WHERE e = %s AND a = %s "
                    "ORDER BY seq DESC LIMIT 1",
                    (e, "balance"),
                )
                row = cur.fetchone()
                out[e] = (
                    _json.loads(row[0]) if row and row[0] is not None
                    else None
                )
        return out["X"], out["Y"]
    finally:
        conn.close()


def audit_chain_continuous(*, dsn: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` — verify audit-chain Merkle continuity.

    Walks audit datoms in ``seq`` order; each datom's
    ``provenance[':prev-hash']`` must equal the prior datom's
    ``provenance[':signature']`` (or ``None`` for the first). Also
    confirms ``audit_chain_lock.last_hash`` equals the chain tip's
    signature, when audit datoms exist.

    Returns:
        ``(True, "ok")`` on continuous chain.
        ``(False, <description>)`` on first break.
    """
    import psycopg
    import json as _json

    conn = psycopg.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, provenance FROM datom_log "
                "WHERE a LIKE 'audit/%' ORDER BY seq ASC"
            )
            rows = cur.fetchall()
            if not rows:
                return True, "no audit datoms"
            prev_sig: str | None = None
            for seq, prov_text in rows:
                prov = _json.loads(prov_text) if prov_text else {}
                got_prev = prov.get(":prev-hash")
                # Normalise: missing/empty -> None.
                if got_prev == "":
                    got_prev = None
                if got_prev != prev_sig:
                    return (
                        False,
                        f"chain break at seq={seq}: "
                        f"prev-hash={got_prev!r} != expected={prev_sig!r}",
                    )
                sig = prov.get(":signature")
                if not isinstance(sig, str):
                    return False, f"missing signature at seq={seq}"
                prev_sig = sig
            # Verify lock-row tail-hash equals chain tip.
            cur.execute("SELECT last_hash FROM audit_chain_lock WHERE id = 1")
            tail_row = cur.fetchone()
            tail_hash = tail_row[0] if tail_row else ""
            if tail_hash and tail_hash != prev_sig:
                return (
                    False,
                    f"audit_chain_lock.last_hash={tail_hash!r} != "
                    f"chain-tip signature={prev_sig!r}",
                )
        return True, "ok"
    finally:
        conn.close()


# Re-exports for ergonomic test imports.
__all__ = [
    "CommitResult",
    "IsolationLevel",
    "WriterSpec",
    "spawn_two_writers",
    "seed_xy_balances",
    "reserve_two_tx_ids",
    "read_final_xy",
    "audit_chain_continuous",
]
