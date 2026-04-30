"""`:code/exec` sandbox handler — Phase 2.0b / #141.

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md``
§ 4.2 + § 3.7 (replay-table row ``:code/exec``) + ADR-5
(capability-denial-not-detection) for the ground-truth design.

## Public surface

- :class:`CodeExecResult` — frozen dataclass returned by :func:`exec_code`.
- :func:`exec_code` — synchronously execute a Python source snippet in
  a sandboxed subprocess and return a :class:`CodeExecResult`. Must be
  called inside an active ``db.dosync(...)`` body so the audit datom
  rides the existing Merkle chain (mirrors the Plan-Edit invariant
  from #140 / ADR-6).
- :func:`make_code_exec_handler` — register a no-op terminator handler
  for ``:code/exec`` so the audit handler (which wraps ``:code/exec``
  as middleware) has a raw handler underneath. The terminator is a
  no-op because the side-effect (subprocess execution) ALREADY ran
  inside :func:`exec_code` — the ``tx.effect()`` queued at intent-replay
  time exists solely to emit the audit datom.

## Capability-denial layers (ADR-5)

1. **Subprocess isolation** — fresh interpreter via ``sys.executable -I -S``,
   never ``eval`` / ``exec`` in-process.
2. **POSIX ``setrlimit`` preexec hook** — RLIMIT_CPU / RLIMIT_AS /
   RLIMIT_NOFILE / RLIMIT_NPROC / RLIMIT_FSIZE caps on the child.
3. **Wall-clock timeout** — ``proc.communicate(timeout=...)`` + kill
   on ``TimeoutExpired``.
4. **Module allowlist** — bootstrap shim inside the child monkey-patches
   ``builtins.__import__`` so only ``json`` / ``re`` / ``dataclasses`` /
   ``pathlib`` (and their measured transitive stdlib closure) are
   importable. Everything else raises :class:`CodeExecForbiddenImport`-
   shaped ``ImportError`` from inside the child; the parent surfaces
   it via a stderr marker line.
5. **No network** — ``socket`` is blocked at import; we do NOT add a
   netns dance (capability-denial, not detection).
6. **Working dir** — fresh ``tempfile.mkdtemp()`` cleaned up on exit;
   ``RLIMIT_FSIZE=0`` makes the child effectively read-only on disk.

## Audit datom shape (rides the existing Merkle chain via ``tx.effect``)

The seven datom keys are:

- ``:code/exec/source-hash`` (sha256 of source bytes)
- ``:code/exec/stdin-hash`` (sha256 of stdin bytes)
- ``:code/exec/output-hash`` (sha256 of canonical-JSON of
  ``{stdout, stderr, exit_code}``)
- ``:code/exec/exit-code`` (int)
- ``:code/exec/wall-clock-ms`` (int)
- ``:code/exec/timeout-seconds`` (float)
- ``:code/exec/memory-mb`` (int)

Stdout / stderr full captures are NOT in the datom (potentially huge);
only the hashes are. Audit-replay reads the recorded hashes; re-execution
replay re-runs and verifies ``output_hash`` matches.

## Replay semantics (§ 3.7)

- **Audit-replay (default)** — caller passes ``replay_mode="audit"`` (or
  omits it; default is execute). Audit-replay is invoked separately from
  this surface; the recorded hashes in the datom are sufficient.
- **Re-execution-replay (opt-in)** — caller passes
  ``replay_mode="re-execute"``; the source is re-run under the same
  env + memory + timeout, and the recomputed ``output_hash`` is
  verified against ``expected_output_hash`` (also passed in by the
  caller from the recorded datom). Mismatch raises
  :class:`CodeExecReplayMismatch`.

## Platform note (macOS)

``RLIMIT_AS`` (address-space cap) behaves differently on macOS than on
Linux — the kernel often does not honor reductions for processes that
have already mapped large libc segments, so a 128MB cap may silently
pass even on a body that allocates 1GB. The memory-cap test in
``tests/effect/test_code_exec.py`` is skipped on Darwin with a
documented xfail. RLIMIT_FSIZE / RLIMIT_NOFILE / RLIMIT_NPROC /
RLIMIT_CPU are honored on both platforms.
"""
from __future__ import annotations

import dataclasses
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time as _time  # noqa: wall-clock — sandbox timing is OUTSIDE the audit-clock domain (handler-level wall-clock)
from dataclasses import dataclass
from typing import Any

from persistence.effect.canonical import canonical_dumps
from persistence.effect.runtime import Handler

# POSIX-only resource limits for the child (Linux + macOS Darwin).
# On Windows, ``resource`` is unavailable; the handler falls back to
# wall-clock timeout + import-allowlist only — capability-denial via
# rlimit is a no-op there. The platform skip is honored at runtime so
# the test suite doesn't crash on import; non-POSIX gets a documented
# softer guarantee.
try:
    import resource as _resource

    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover — non-POSIX
    _resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CodeExecError(RuntimeError):
    """Base class for :code/exec sandbox errors."""


class CodeExecOutsideDosync(CodeExecError):
    """``exec_code()`` called without an enclosing ``db.dosync(...)`` body.

    Mirrors :class:`persistence.plan.PlanEditOutsideDosync` (#140) — the
    audit datom for a ``:code/exec`` call must ride the same Merkle
    chain as the rest of the trajectory. Outside dosync there is no
    chain to link into; raising here keeps the "no silent execution"
    invariant from ADR-6.
    """


class CodeExecTimeout(CodeExecError):
    """The sandboxed subprocess exceeded ``timeout_seconds`` wall-clock."""

    timeout_seconds: float
    partial_stdout: str

    def __init__(self, timeout_seconds: float, partial_stdout: str) -> None:
        super().__init__(
            f":code/exec subprocess timed out after {timeout_seconds}s "
            f"(captured {len(partial_stdout)} bytes of stdout before kill)"
        )
        self.timeout_seconds = timeout_seconds
        self.partial_stdout = partial_stdout


class CodeExecMemoryExceeded(CodeExecError):
    """The sandboxed subprocess hit the configured ``memory_mb`` cap.

    On Linux RLIMIT_AS reliably triggers a non-zero exit code with
    ``MemoryError`` traceback in stderr. On macOS, RLIMIT_AS often does
    NOT enforce reductions reliably (see module docstring) so this
    exception is currently Linux-only. Callers MUST treat
    ``exit_code != 0`` as the load-bearing cross-platform signal.
    """

    memory_mb: int

    def __init__(self, memory_mb: int) -> None:
        super().__init__(
            f":code/exec subprocess exceeded {memory_mb}MB address-space cap"
        )
        self.memory_mb = memory_mb


class CodeExecForbiddenImport(CodeExecError):
    """The sandboxed body attempted to import a module outside the allowlist.

    The bootstrap shim raises ``ImportError`` with a sentinel prefix
    inside the child; the parent strips that prefix and re-raises
    here so callers can ``except CodeExecForbiddenImport``.

    Allowed-set at v0.5: ``json``, ``re``, ``dataclasses``, ``pathlib``
    plus their measured transitive stdlib closure. Anything outside
    (``os``, ``sys``, ``subprocess``, ``socket``, ``urllib``, ``http``,
    ``ctypes``, ``threading``, ``multiprocessing``, ``pickle``,
    ``marshal``, ``time``, ``random``) is denied at import-time.
    """

    module_name: str

    def __init__(self, module_name: str) -> None:
        super().__init__(
            f":code/exec sandbox forbidden import: {module_name!r} is not "
            f"on the allowlist (json, re, dataclasses, pathlib only)"
        )
        self.module_name = module_name


class CodeExecReplayMismatch(CodeExecError):
    """Re-execution-replay produced a different ``output_hash`` than recorded.

    Only raised by :func:`exec_code` when ``replay_mode="re-execute"``
    AND ``expected_output_hash`` is provided. ``:code/exec`` is the one
    effect with first-class re-execution support per § 3.7.
    """

    expected_hash: str
    actual_hash: str

    def __init__(self, expected_hash: str, actual_hash: str) -> None:
        super().__init__(
            f":code/exec re-execution replay mismatch: "
            f"expected {expected_hash!r}, got {actual_hash!r}"
        )
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeExecResult:
    """Outcome of a single ``:code/exec`` invocation.

    All four content fields (``stdout``, ``stderr``, ``exit_code``,
    ``wall_clock_ms``) are inputs to ``output_hash`` via canonical-JSON
    serialisation BUT ``wall_clock_ms`` is excluded from the hash by
    design — the byte-identity invariant is over the (stdout, stderr,
    exit_code) triple, NOT over latency.
    """

    stdout: str
    stderr: str
    exit_code: int
    wall_clock_ms: int
    output_hash: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _sha256_bytes(payload: bytes) -> str:
    """Return ``"sha256:<hex>"`` of ``payload`` (raw-bytes hash)."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _output_hash(stdout: str, stderr: str, exit_code: int) -> str:
    """Canonical-JSON hash of the captured output triple.

    Excludes ``wall_clock_ms`` — wall-clock IS expected to vary
    between two runs of the same source (CPU contention, GC, etc.).
    """
    payload = canonical_dumps(
        {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Subprocess scaffolding (Commit 1 — skeleton; setrlimit + import filter
# come in Commits 2 + 3)
# ---------------------------------------------------------------------------


def _build_child_command() -> list[str]:
    """Return the argv for the sandboxed child interpreter.

    ``-I`` (isolated): suppress PYTHON* env, user site-packages, sys.path[0]
    insertion, ``-i`` interactive escapes. ``-S`` (no site): skip ``site.py``
    so user-installed packages cannot leak into the child.

    The two flags together turn the child into a near-pristine stdlib
    interpreter; combined with the import-filter shim (Commit 3) the
    body sees only the four allowlisted modules.
    """
    return [sys.executable, "-I", "-S", "-c", _CHILD_RUNNER_BOOTSTRAP]


def _make_preexec(timeout_seconds: float, memory_mb: int) -> Any:
    """Build a ``preexec_fn`` callable that applies POSIX ``setrlimit`` caps.

    Runs in the child between ``fork()`` and ``exec()``, BEFORE any user
    code starts; the kernel enforces these caps for the lifetime of the
    sandboxed interpreter.

    Caps applied:

    - ``RLIMIT_CPU`` = ``timeout_seconds + 1`` — kernel sends SIGXCPU /
      SIGKILL on overrun (orthogonal to wall-clock; stops infinite-loop
      bodies even if our parent-side timeout misfires).
    - ``RLIMIT_AS`` = ``memory_mb * 1024 * 1024`` — address-space cap.
      Linux honors reductions; macOS often does NOT (kernel may have
      already mapped > cap of libc segments before fork). Best-effort
      cross-platform; the memory-cap test is platform-skipped on Darwin.
    - ``RLIMIT_NOFILE`` = ``32`` — fd cap; prevents fd-flood DoS.
    - ``RLIMIT_NPROC`` = ``1`` — no fork bombs (the child cannot spawn
      further processes).
    - ``RLIMIT_FSIZE`` = ``0`` — no file writes from the child;
      combined with the working-dir tempdir this makes the child
      effectively read-only on disk.

    Returns ``None`` on non-POSIX platforms (``resource`` unavailable);
    the caller must check ``_HAS_RESOURCE`` and pass ``None`` for
    ``preexec_fn`` in that case.

    Note: the function is closure-captured by Popen and runs in the
    forked child; any exception inside it kills the child before exec().
    Failures are swallowed (best-effort) to avoid masking the parent's
    actual error path; in production a ``setrlimit`` failure means the
    child runs with default limits — still bounded by wall-clock.
    """
    if not _HAS_RESOURCE:
        return None

    cpu_limit = int(timeout_seconds) + 1
    as_limit = max(1, int(memory_mb)) * 1024 * 1024

    def _preexec() -> None:
        # Best-effort each cap. The setrlimit calls can fail when:
        # - memory_mb is so small that the interpreter cannot start
        #   (so Popen fails fast, surfaced as exit_code != 0)
        # - macOS RLIMIT_AS sometimes returns EINVAL on reduction;
        #   callers see exit_code 0 with no enforcement, hence the
        #   platform-skip on the memory test.
        try:
            _resource.setrlimit(  # type: ignore[union-attr]
                _resource.RLIMIT_CPU, (cpu_limit, cpu_limit)  # type: ignore[union-attr]
            )
        except (ValueError, OSError):
            pass
        try:
            _resource.setrlimit(  # type: ignore[union-attr]
                _resource.RLIMIT_AS, (as_limit, as_limit)  # type: ignore[union-attr]
            )
        except (ValueError, OSError):
            pass
        try:
            _resource.setrlimit(  # type: ignore[union-attr]
                _resource.RLIMIT_NOFILE, (32, 32)  # type: ignore[union-attr]
            )
        except (ValueError, OSError):
            pass
        try:
            _resource.setrlimit(  # type: ignore[union-attr]
                _resource.RLIMIT_NPROC, (1, 1)  # type: ignore[union-attr]
            )
        except (ValueError, OSError):
            pass
        try:
            # FSIZE=0 means any write() call from the child gets SIGXFSZ.
            # The child is allowed to read /usr/lib/* etc. (file opens
            # for reading aren't size-bounded); only writes are killed.
            _resource.setrlimit(  # type: ignore[union-attr]
                _resource.RLIMIT_FSIZE, (0, 0)  # type: ignore[union-attr]
            )
        except (ValueError, OSError):
            pass

    return _preexec


# Commit 1 placeholder — Commits 3+ replace this with a real bootstrap
# shim that installs the import filter and exec()s the user source.
_CHILD_RUNNER_BOOTSTRAP = (
    "import sys\n"
    "source = sys.stdin.read()\n"
    "exec(compile(source, '<sandbox>', 'exec'), {'__name__': '__sandbox__'})\n"
)


def _run_subprocess(
    source: str,
    stdin: str,
    timeout_seconds: float,
    memory_mb: int,
    env: dict[str, str] | None,
) -> tuple[str, str, int, int]:
    """Run ``source`` in a sandboxed subprocess.

    Returns ``(stdout, stderr, exit_code, wall_clock_ms)``. The bootstrap
    shim reads the user source from stdin (Commit 1 simple form). Commits
    3+ replace stdin with an envelope that splits user-stdin from source.
    """
    cmd = _build_child_command()

    workdir = tempfile.mkdtemp(prefix="persistence-code-exec-")

    child_env: dict[str, str] = {}
    if env:
        child_env.update(env)

    # Commit 1: stdin carries source only; user-supplied stdin ignored
    # until Commit 3 introduces the envelope protocol.
    stdin_payload = source

    preexec = _make_preexec(timeout_seconds=timeout_seconds, memory_mb=memory_mb)

    t0 = _time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            env=child_env,
            text=True,
            preexec_fn=preexec,  # POSIX-only setrlimit caps; None on non-POSIX
        )
    except (OSError, ValueError) as exc:  # pragma: no cover
        shutil.rmtree(workdir, ignore_errors=True)
        raise CodeExecError(f":code/exec subprocess spawn failed: {exc}") from exc

    try:
        try:
            stdout, stderr = proc.communicate(
                input=stdin_payload, timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            partial_stdout, _partial_stderr = proc.communicate()
            raise CodeExecTimeout(
                timeout_seconds=timeout_seconds,
                partial_stdout=partial_stdout or "",
            )
        wall_clock_ms = int((_time.monotonic() - t0) * 1000)
        return stdout, stderr, int(proc.returncode), wall_clock_ms
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Public API (skeleton — Commit 4 wires the audit datom emission)
# ---------------------------------------------------------------------------


def exec_code(
    source: str,
    *,
    stdin: str = "",
    timeout_seconds: float = 5.0,
    memory_mb: int = 128,
    env: dict[str, str] | None = None,
    tx: Any = None,
    replay_mode: str = "execute",
    expected_output_hash: str | None = None,
) -> CodeExecResult:
    """Execute ``source`` in the capability-denied sandbox; return outcome.

    Args:
        source: Python source code (single module body).
        stdin: stdin payload fed to the body. Default empty.
        timeout_seconds: wall-clock cap. Subprocess killed on overrun.
        memory_mb: address-space cap (Linux-honored, macOS best-effort).
        env: explicit env dict for the child (default empty; parent's
            env is NEVER inherited).
        tx: the active ``Transaction``; required.
        replay_mode: ``"execute"`` (default) or ``"re-execute"`` —
            re-execute also verifies ``output_hash`` against
            ``expected_output_hash``.
        expected_output_hash: required when ``replay_mode="re-execute"``.

    Returns:
        :class:`CodeExecResult` with full stdout/stderr captures + hashes.

    Raises:
        CodeExecOutsideDosync: when called without a live dosync OR
            when ``tx`` is ``None``.
        CodeExecTimeout, CodeExecMemoryExceeded, CodeExecForbiddenImport,
        CodeExecReplayMismatch, CodeExecError: see class docstrings.
    """
    from persistence.txn.intents import is_in_dosync

    if not is_in_dosync() or tx is None:
        raise CodeExecOutsideDosync(
            "exec_code() must run inside a db.dosync(...) body with the "
            "active Transaction passed via the tx= keyword. The :code/exec "
            "audit datom rides the existing Merkle chain at "
            "effect/handlers/audit.py via tx.effect(); without the "
            "enclosing txn, the call would be a silent unaudited execution "
            "(violates ADR-6 / § 3.7)."
        )

    if replay_mode not in ("execute", "re-execute"):
        raise ValueError(
            f"replay_mode must be 'execute' or 're-execute', got "
            f"{replay_mode!r}"
        )
    if replay_mode == "re-execute" and expected_output_hash is None:
        raise ValueError(
            "replay_mode='re-execute' requires expected_output_hash= "
            "(the recorded :code/exec/output-hash from the audit datom)"
        )

    stdout, stderr, exit_code, wall_clock_ms = _run_subprocess(
        source=source,
        stdin=stdin,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
        env=env,
    )

    output_hash = _output_hash(stdout, stderr, exit_code)

    if replay_mode == "re-execute":
        if output_hash != expected_output_hash:
            raise CodeExecReplayMismatch(
                expected_hash=str(expected_output_hash),
                actual_hash=output_hash,
            )

    return CodeExecResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        wall_clock_ms=wall_clock_ms,
        output_hash=output_hash,
    )


# ---------------------------------------------------------------------------
# Effect-runtime handler (no-op terminator for :code/exec audit chaining)
# ---------------------------------------------------------------------------


def make_code_exec_handler() -> Handler:
    """Return a no-op terminator handler for ``:code/exec``.

    The audit handler (``make_audit_handler(wraps=":code/exec")``) is
    middleware: it calls ``k(args)`` to delegate downstream. Without
    a terminator below it, the runtime raises ``Unhandled``. This
    factory provides the terminator.

    Why a no-op? Because the side-effect (subprocess execution) ALREADY
    ran inside :func:`exec_code` BEFORE the ``tx.effect()`` was queued.
    The intent-replay-time perform call exists solely so the audit
    handler emits the AuditEntry with the captured hashes; there is no
    further work to do here.

    Mirrors the ``_noop_plan_edit_raw_handler`` pattern from
    ``tests/plan/test_edit_audit.py``.
    """
    return Handler(
        name="code-exec-raw",
        wraps={":code/exec"},
        clauses={":code/exec": lambda _args, _k, _ctx: None},
    )


__all__ = [
    "CodeExecError",
    "CodeExecForbiddenImport",
    "CodeExecMemoryExceeded",
    "CodeExecOutsideDosync",
    "CodeExecReplayMismatch",
    "CodeExecResult",
    "CodeExecTimeout",
    "exec_code",
    "make_code_exec_handler",
]
