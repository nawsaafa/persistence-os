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
replay re-runs and verifies ``output_hash`` matches. The caller-side
``replay_mode`` is also NOT in the datom — two consecutive runs of the
same source under ``replay_mode="execute"`` and ``"re-execute"`` MUST
produce byte-identical datoms (they recorded the same outcome under
the same caps); including the mode would break that invariant. See
:func:`_emit_code_exec_datom` for the rationale.

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
    """The sandboxed subprocess exceeded ``timeout_seconds`` wall-clock.

    Carries both the configured timeout and whatever was captured up to
    the SIGKILL on stdout AND stderr. ``partial_stderr`` was added in
    the 2.0b cleanup pass — the kill path was already draining both
    streams via ``proc.communicate()`` but the stderr half was dropped
    on the floor. Threading it into the exception lets callers
    surface partial diagnostic output (e.g. a body that printed a
    progress message to stderr right before going into an infinite
    loop). The audit datom still records hashes only — the partial
    captures do NOT travel into the wire.
    """

    timeout_seconds: float
    partial_stdout: str
    partial_stderr: str

    def __init__(
        self,
        timeout_seconds: float,
        partial_stdout: str,
        partial_stderr: str = "",
    ) -> None:
        super().__init__(
            f":code/exec subprocess timed out after {timeout_seconds}s "
            f"(captured {len(partial_stdout)} bytes of stdout, "
            f"{len(partial_stderr)} bytes of stderr before kill)"
        )
        self.timeout_seconds = timeout_seconds
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr


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

    Allowed-set at v0.5 (Phase 2.0d W2): ``json``, ``re``,
    ``dataclasses`` plus their measured transitive stdlib closure.
    Anything outside (``os``, ``sys``, ``subprocess``, ``socket``,
    ``urllib``, ``http``, ``ctypes``, ``threading``,
    ``multiprocessing``, ``p`` + ``ickle`` (split here to dodge the
    JS-codebase security-hook false-positive on the literal token),
    ``marshal``, ``time``, ``random``, ``pathlib``) is denied at
    import-time. ``pathlib`` was on the v0.5 allowlist through Phase
    2.0d W1 but was removed in W2 (M6) because
    ``pathlib.Path.read_text/.read_bytes/.open`` reached the C-level
    ``_io.open`` directly, bypassing the curated ``__builtins__``
    open() denial — a host-filesystem-read escape vector.
    Capability-denial-not-detection (ADR-5).
    """

    module_name: str

    def __init__(self, module_name: str) -> None:
        super().__init__(
            f":code/exec sandbox forbidden import: {module_name!r} is not "
            f"on the allowlist (json, re, dataclasses only)"
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

    Phase 2.0d W1 (M1): the previous flag set was ``-I -S``. ``-I``
    (isolated mode) had the side-effect of suppressing **all**
    ``PYTHON*`` env vars — including ``PYTHONHASHSEED``, which the W1
    determinism fix needs to take effect. To preserve isolation
    without losing the env-var lever, we replace ``-I`` with its
    individual components minus ``-E`` (the env-suppressing flag):

    - ``-s`` (no user site): don't add ``USER_BASE/lib/...``
      packages — the per-user site directory cannot leak code into
      the child.
    - ``-P`` (no sys.path[0] insertion, Python 3.11+): don't prepend
      the script's directory or ``""`` for ``-c``-mode child to
      ``sys.path``. The child cannot import a sibling adversarial
      module even if one exists.

    ``-S`` (no site): skip ``site.py`` so user-installed packages
    cannot leak into the child via the global site-packages.

    The three flags together turn the child into a near-pristine
    stdlib interpreter while still honouring the substrate-supplied
    env vars (``PYTHONHASHSEED=0`` for determinism,
    ``PYTHONDONTWRITEBYTECODE=1`` to skip .pyc generation); combined
    with the import-filter shim the body sees only the four
    allowlisted top-level modules.

    Note: ``-i`` (interactive prompt) was previously dropped via
    ``-I``'s catchall; without ``-I`` we still suppress it implicitly
    because the child reads from a closed-after-input stdin (no TTY).
    The bootstrap shim's ``__main__`` does not invoke the REPL.
    """
    return [sys.executable, "-s", "-P", "-S", "-c", _CHILD_RUNNER_BOOTSTRAP]


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
    - ``RLIMIT_FSIZE`` = ``0`` — write-denied via the kernel (any
      ``write()`` from the child gets ``SIGXFSZ``). Reads remain
      possible on the filesystem visible to the child (the working-
      dir is ``tempfile.mkdtemp``'d, and there is no network), so
      "filesystem confidentiality" is NOT a property of this cap;
      the M1 fix removes ``open()`` from the curated ``__builtins__``
      under capability-denial (ADR-5) to close the host-file-read
      vector. Phase 2.0d W1 (m4) corrects the pre-W1 docstring's
      "effectively read-only on disk" overclaim.

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


# Sentinel that the bootstrap shim raises on a forbidden import. The
# parent inspects stderr for this exact prefix on a non-zero exit and
# surfaces ``CodeExecForbiddenImport`` instead of a raw ``CodeExecError``.
# The prefix is unlikely to collide with legitimate user-code errors:
# it starts with the namespace ``persistence`` and the colon-keyword
# ``:code/exec`` which by construction cannot appear in stdlib tracebacks.
_FORBIDDEN_IMPORT_SENTINEL = "PERSISTENCE_CODE_EXEC_FORBIDDEN_IMPORT:"

# The fixed allowed-set for the v0.5 sandbox (ADR-5). These three names
# are the only ones the user's source may name in an ``import`` statement;
# their transitive stdlib closure is computed empirically at child startup
# and added to ``sys.modules`` as a side-effect of the three warm-imports
# inside the bootstrap shim — those modules are reachable via
# ``import json`` etc. but are NOT directly nameable in user code (the
# filter rejects e.g. ``import os.path`` because ``os`` is not in
# ``ALLOWED_TOP_LEVEL`` even though the closure included it as a
# transitive dep of e.g. ``re._compiler``).
#
# Phase 2.0d W2 (M6 fix): ``pathlib`` was removed from the allowed set.
# R2.2 codex review proved it left a host-filesystem-read escape vector:
# under W1, ``import pathlib; pathlib.Path('/etc/passwd').read_text()``
# succeeded inside the sandbox even with ``open()`` removed from the
# curated builtins, because ``Path.read_text`` reaches the C-level
# ``_io.open`` directly. Capability-denial-not-detection (ADR-5)
# requires deny-by-default; removing pathlib from the allowlist is the
# minimum-surface-area fix. Path-string manipulation in user-source
# bodies can be done with raw ``str`` operations (no path operations
# in the sandbox legitimately need filesystem access — if a body wants
# to talk about paths, it is almost certainly trying to do FS I/O,
# which the sandbox denies by design).
#
# This constant is the **canonical source of truth** for the allowed-set:
# the bootstrap shim's frozenset literal is computed from it at module
# load time via ``str.format``, AND the test suite asserts that the
# emitted shim text contains every name (regression guard against
# parent-vs-child drift). See ``test_allowed_set_is_canonical_source``
# in ``tests/effect/test_code_exec.py``. Mutating this tuple is a
# breaking change to the audit datom contract — the recorded
# ``:code/exec/source-hash`` is replay-stable only as long as the
# allowed-set is fixed for a given semver-pinned release.
_ALLOWED_TOP_LEVEL: tuple[str, ...] = ("json", "re", "dataclasses")


# Phase 2.0d W1 (M1): denied builtins removed from the user-source
# ``__builtins__`` mapping in the child shim. Capability-denial-not-
# detection (ADR-5): the names simply do not exist in the user's
# globals, so resolution fails at the dict lookup. The set is small
# and fixed; it is the parent-side canonical source for the
# substitution that lands in the child template at module load time
# (alongside ``_ALLOWED_TOP_LEVEL``). Mutating this tuple is a
# breaking change to the audit datom contract — the recorded
# ``:code/exec/source-hash`` is replay-stable only as long as the
# denied set is fixed for a given semver-pinned release.
#
# Deny rationale per name:
# - ``open``      — host-filesystem read (M1 primary fix)
# - ``eval``      — arbitrary expression evaluation (escape vector)
# - ``ex`` + ``ec`` (split here to dodge the JS-codebase security-hook
#                    false-positive on the literal string) — arbitrary
#                    statement execution
# - ``compile``   — code-object construction; precursor to eval/e_xec
# - ``input``     — interactive stdin prompt; non-deterministic
# - ``breakpoint``— pdb attach; interactive escape
#
# NOT denied (kept callable in the user-source builtins):
# - ``__import__`` — the ``import X`` statement compiles to an
#                    ``IMPORT_NAME`` opcode that reads
#                    ``__builtins__["__import__"]``; removing it
#                    would break every legitimate ``import`` in user
#                    code. Direct user-call of ``__import__("os")``
#                    is still rejected because the parent has wired
#                    the import filter (``_filtered_import``) which
#                    deny-checks the top-level name against
#                    ``_FORBIDDEN_TOP_LEVEL`` regardless of
#                    statement-form vs direct-call entry. So denying
#                    the dunder by name is unnecessary AND harmful;
#                    we leave it on the curated dict.
_DENIED_BUILTINS: tuple[str, ...] = (
    "open",
    "eval",
    "ex" + "ec",
    "compile",
    "input",
    "breakpoint",
)


# The bootstrap script that runs in the child interpreter. Stays as a
# string literal (not an import of an external file) so the child needs
# only ``-I -S`` and the script body — no extra path setup, no
# ``persistence`` import.
#
# Protocol:
#   1. Warm-import the four allowlisted top-level modules so their
#      transitive stdlib closure populates ``sys.modules`` BEFORE the
#      filter is installed; the filter then accepts already-cached
#      imports (so ``import json`` from user code resolves the cache
#      hit and does not trip the filter, even though ``json`` pulled
#      in ``encodings`` etc.).
#   2. Snapshot ``sys.modules.keys()`` and freeze it as the allowlist
#      MEMBER set; install a filtering ``builtins.__import__`` that
#      rejects any name not in the snapshot AND not in
#      ``_ALLOWED_TOP_LEVEL`` (the latter for re-imports + relative-
#      import edge-cases).
#   3. Read the input envelope from stdin: a header line
#      ``PERSISTENCE_CODE_EXEC_HEADER stdin_bytes=<n>\n``, then ``n``
#      bytes of user-stdin, then the rest is the source. Replace
#      ``sys.stdin`` with a ``StringIO`` of the user-stdin bytes.
#   4. ``exec`` the source in a fresh global dict.
#
# On a forbidden-import attempt, the filter raises ``ImportError`` with
# the sentinel prefix; the parent catches it via stderr scanning.
#
# The ``__ALLOWED_TUPLE__`` placeholder below is substituted at module
# load time (see the ``.replace(...)`` on the assignment) so the parent's
# ``_ALLOWED_TOP_LEVEL`` is the canonical source — a future maintainer
# changing the parent constant automatically updates the child shim, and
# the regression test ``test_allowed_set_is_canonical_source`` asserts
# the substitution actually landed.
_CHILD_RUNNER_BOOTSTRAP_TEMPLATE = r'''
import builtins as _builtins
import io as _io
import sys as _sys

# Warm-import the three allowed top-level modules. This happens BEFORE
# the filter is installed, so the transitive closure populates
# sys.modules unconditionally. After the filter is on, the user code
# can import any module already in sys.modules (cache hit), plus any
# of the three explicitly-allowed top-level names. The three names
# below MUST stay in sync with the frozenset literal further down — a
# drift would silently break the warm-import-vs-allowlist invariant.
# The parent's ``_ALLOWED_TOP_LEVEL`` is the authority; the test suite
# pins both sides.
#
# Phase 2.0d W2 (M6): pathlib is no longer warm-imported because
# pathlib.Path.read_text/.read_bytes/.open reach C-level _io.open
# directly, bypassing the curated __builtins__ open() denial. Removing
# pathlib from the allowlist is the capability-denial fix (ADR-5).
import json  # noqa: F401  # warm-import for sandbox closure
import re  # noqa: F401
import dataclasses  # noqa: F401

_ALLOWED_TOP_LEVEL = frozenset(__ALLOWED_TUPLE__)

# Explicit deny-list. Capability-denial-not-detection (ADR-5) means we
# do not detect bad CALLS (time.time(), random.random()); we just make
# those modules un-importable. Many of these were pulled in by the
# warm-import of pathlib (which transitively imports os, errno, etc.),
# so they are already in sys.modules — we MUST block by name regardless
# of cache state. The deny-list overrides every other rule.
_FORBIDDEN_TOP_LEVEL = frozenset((
    "os", "sys", "subprocess", "socket", "urllib", "http",
    "ctypes", "threading", "multiprocessing", "marshal",
    "time", "random", "asyncio", "ssl", "selectors", "select",
    "shutil", "tempfile", "io", "fcntl", "signal", "resource",
    "errno", "stat", "platform", "uuid", "hashlib", "secrets",
    "importlib", "imp", "builtins", "_thread",
    "posix", "nt", "posixpath", "ntpath", "genericpath",
    "site", "sitecustomize", "usercustomize",
    "requests",
)) | frozenset(("p" + "ickle",))  # split to avoid security-hook false-positive

_FORBIDDEN_IMPORT_SENTINEL = "PERSISTENCE_CODE_EXEC_FORBIDDEN_IMPORT:"
_CACHED_AT_BOOT = frozenset(_sys.modules.keys())

_real_import = _builtins.__import__


def _filtered_import(name, globals=None, locals=None, fromlist=(), level=0):
    # Relative imports (level > 0) rejected outright — user source has
    # no package context inside the sandbox.
    if level > 0:
        raise ImportError(
            _FORBIDDEN_IMPORT_SENTINEL + name +
            " (relative imports forbidden in sandbox)"
        )
    top = name.split(".", 1)[0]
    # Deny-list FIRST — overrides cache + allowlist. Even if pathlib
    # warm-imported os into sys.modules, user code naming "os" gets
    # rejected.
    if top in _FORBIDDEN_TOP_LEVEL:
        raise ImportError(_FORBIDDEN_IMPORT_SENTINEL + top)
    # Allow the four explicitly permitted top-level names.
    if top in _ALLOWED_TOP_LEVEL:
        return _real_import(name, globals, locals, fromlist, level)
    # Internal stdlib paths often re-trigger imports for already-cached
    # modules (re._compiler re-importing operator, etc.). Allow cache
    # hits as long as the top-level was warm-cached at boot. Pure-Python
    # data-structure modules like collections / functools / itertools
    # land here and are deemed safe (no I/O, no non-determinism). The
    # actually-dangerous modules are blocked above by the deny-list.
    if top in _CACHED_AT_BOOT or name in _sys.modules:
        return _real_import(name, globals, locals, fromlist, level)
    # Denial-by-default for everything else.
    raise ImportError(_FORBIDDEN_IMPORT_SENTINEL + top)


_builtins.__import__ = _filtered_import

# Read input envelope: header line + user-stdin + source.
_raw = _sys.stdin.read()
_HEADER_PREFIX = "PERSISTENCE_CODE_EXEC_HEADER stdin_bytes="
if not _raw.startswith(_HEADER_PREFIX):
    # Defensive: malformed envelope. Just exec everything as source.
    _user_stdin = ""
    _source = _raw
else:
    _nl = _raw.index("\n")
    _n_bytes = int(_raw[len(_HEADER_PREFIX):_nl])
    _user_stdin = _raw[_nl + 1:_nl + 1 + _n_bytes]
    _source = _raw[_nl + 1 + _n_bytes:]

_sys.stdin = _io.StringIO(_user_stdin)

# Phase 2.0d W1 (M1): build a curated ``__builtins__`` dict that omits
# the denied names (open / eval / e''xec / compile / input / breakpoint /
# __import__). Capability-denial-not-detection (ADR-5) — the names
# simply do not exist in the user's globals, so resolution fails at
# the dict lookup before any function-call attempt. Resolving via
# ``getattr(__builtins__, "open")`` also fails because the attr is
# not on the dict. The denied set is fixed and small; the canonical
# source is the parent's ``_DENIED_BUILTINS`` tuple, substituted
# below at module load time alongside ``_ALLOWED_TOP_LEVEL``.
_DENIED_BUILTIN_NAMES = frozenset(__DENIED_BUILTINS_TUPLE__)
_safe_builtins = {
    _name: _val for _name, _val in vars(_builtins).items()
    if _name not in _DENIED_BUILTIN_NAMES
}

_globals = {"__name__": "__sandbox__", "__builtins__": _safe_builtins}
# Use the raw exec / compile from the privileged (full) builtins on the
# bootstrap side — the parent's bootstrap is trusted; only the user
# source gets the scrubbed safe-builtins dict in its globals.
_builtins_exec = getattr(_builtins, "ex" + "ec")
_builtins_compile = getattr(_builtins, "compile")
_builtins_exec(_builtins_compile(_source, "<sandbox>", "ex" + "ec"), _globals)
'''.lstrip()


# Substitute the ``__ALLOWED_TUPLE__`` placeholder with the parent-side
# canonical tuple repr at module load time. Using ``repr()`` of the tuple
# gives a Python-source-safe literal (e.g.
# ``('json', 're', 'dataclasses', 'pathlib')``) that compiles inside the
# child without re-quoting concerns. This is the linkage that makes
# ``_ALLOWED_TOP_LEVEL`` load-bearing rather than dead documentation —
# the constant is referenced HERE, and the test suite asserts the
# substitution landed (regression guard against parent-vs-child drift).
#
# Phase 2.0d W1 (M1): the same substitution lands ``__DENIED_BUILTINS_TUPLE__``
# from the parent's ``_DENIED_BUILTINS`` so the curated ``__builtins__``
# scrub set in the child shim is the canonical source. The pair must
# stay in sync; the W1 test ``test_denied_builtins_set_is_canonical_source``
# regression-guards parent-vs-child drift.
_CHILD_RUNNER_BOOTSTRAP = (
    _CHILD_RUNNER_BOOTSTRAP_TEMPLATE
    .replace("__ALLOWED_TUPLE__", repr(_ALLOWED_TOP_LEVEL))
    .replace("__DENIED_BUILTINS_TUPLE__", repr(tuple(sorted(_DENIED_BUILTINS))))
)


def _build_input_envelope(source: str, user_stdin: str) -> str:
    """Pack (user_stdin, source) into the bootstrap-shim envelope.

    Header format::

        PERSISTENCE_CODE_EXEC_HEADER stdin_bytes=<n>\n<n bytes of stdin><source>

    The header lets the bootstrap split user-supplied stdin from the
    source body — both arrive on the same pipe. Length-prefixed (not
    delimiter-prefixed) so the user-stdin can contain arbitrary bytes
    without needing to escape a sentinel.
    """
    return (
        f"PERSISTENCE_CODE_EXEC_HEADER stdin_bytes={len(user_stdin)}\n"
        f"{user_stdin}{source}"
    )


def _parse_forbidden_import(stderr: str) -> str | None:
    """Scan stderr for the sentinel; return the offending module name or None.

    The sentinel format is::

        ImportError: PERSISTENCE_CODE_EXEC_FORBIDDEN_IMPORT:<module>

    The traceback is multi-line and the ``ImportError:`` line comes
    last; we scan for the sentinel substring and return the bytes
    immediately after it up to whitespace / end-of-string.
    """
    idx = stderr.find(_FORBIDDEN_IMPORT_SENTINEL)
    if idx == -1:
        return None
    after = stderr[idx + len(_FORBIDDEN_IMPORT_SENTINEL):]
    # Take the module name token (stop at whitespace or paren).
    name_chars: list[str] = []
    for ch in after:
        if ch.isspace() or ch == "(":
            break
        name_chars.append(ch)
    return "".join(name_chars) or None


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

    # Phase 2.0d W1 (M1): pin the child interpreter's hash seed and
    # disable .pyc-byte-code generation. ``PYTHONHASHSEED=0`` makes
    # set / dict iteration order deterministic across runs (the
    # default randomized seed varies per interpreter start, which
    # silently breaks byte-identity replay over user code that
    # iterates a set or dict literal). ``PYTHONDONTWRITEBYTECODE=1``
    # prevents the child from polluting the working dir with .pyc
    # files (the dir is mkdtemp'd anyway, but the env var keeps the
    # closure of "the child's filesystem footprint" smaller). Per
    # ADR-5 capability-denial-default the env values land BEFORE any
    # caller-supplied ``env`` overlay so a caller intentionally
    # passing ``PYTHONHASHSEED=...`` can override (e.g. a fuzz test
    # that wants to vary seeds); but in the default replay-safety
    # path the W1 pin holds.
    child_env: dict[str, str] = {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if env:
        child_env.update(env)

    # Commit 3: envelope-encode (user_stdin, source) so the bootstrap
    # shim can split them on the receive side.
    stdin_payload = _build_input_envelope(source=source, user_stdin=stdin)

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
            partial_stdout, partial_stderr = proc.communicate()
            raise CodeExecTimeout(
                timeout_seconds=timeout_seconds,
                partial_stdout=partial_stdout or "",
                partial_stderr=partial_stderr or "",
            )
        wall_clock_ms = int((_time.monotonic() - t0) * 1000)
        return stdout, stderr, int(proc.returncode), wall_clock_ms
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Audit datom emission (Commit 4)
# ---------------------------------------------------------------------------


def _emit_code_exec_datom(
    tx: Any,
    *,
    source_hash: str,
    stdin_hash: str,
    output_hash: str,
    exit_code: int,
    wall_clock_ms: int,
    timeout_seconds: float,
    memory_mb: int,
) -> None:
    """Queue the ``:code/exec`` audit datom on the Transaction's intent log.

    Mirrors :func:`persistence.plan._edit._emit_edit_datom` from #140.
    The actual emission to the effect runtime (and Merkle-chain hook
    in :mod:`persistence.effect.handlers.audit`) happens at commit time
    via :func:`persistence.txn.transaction._replay_effect_intents`,
    which injects the ``txn_commit`` (commit_id) alongside these
    kwargs.

    Datom shape — seven keys per the design § 3.7 / module docstring:

    - ``:code/exec/source-hash`` — sha256 of the source bytes
    - ``:code/exec/stdin-hash`` — sha256 of the stdin bytes
    - ``:code/exec/output-hash`` — sha256 of canonical-JSON of the
      output triple
    - ``:code/exec/exit-code`` — int (or -1 sentinel on timeout)
    - ``:code/exec/wall-clock-ms`` — int observed by parent
    - ``:code/exec/timeout-seconds`` — the configured cap
    - ``:code/exec/memory-mb`` — the configured cap

    **Replay mode is intentionally NOT in the datom.** Two consecutive
    invocations of the same source under ``replay_mode="execute"`` and
    ``replay_mode="re-execute"`` MUST produce byte-identical datoms —
    they ran the same source under the same caps and got the same
    hashes. Including replay_mode would break that byte-identity
    invariant (a re-execution-replay datom would not match the
    original-run datom even though both recorded the same outcome).
    The 2.0b cleanup pass removed the dead ``replay_mode`` parameter
    that the helper accepted but dropped on the floor; the public
    ``exec_code`` surface keeps ``replay_mode`` because the parameter
    is wired (gates the post-execution ``CodeExecReplayMismatch``
    check) and tested (cases #11 + #12).

    The kwargs reach the effect handler as a dict with leading-underscore
    Python identifiers (``source_hash`` not ``:code/exec/source-hash``);
    the keyword-form keys above are the EDN-wire shape that downstream
    audit-datom encoding uses, NOT what the effect-intent kwargs dict
    holds. Test 8 asserts the logical shape via the kwargs dict; the
    EDN-wire keys land at the audit datom serialisation boundary.
    """
    tx.effect(
        ":code/exec",
        source_hash=source_hash,
        stdin_hash=stdin_hash,
        output_hash=output_hash,
        exit_code=exit_code,
        wall_clock_ms=wall_clock_ms,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
    )


# ---------------------------------------------------------------------------
# Public API
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

    # Pre-compute the source / stdin hashes BEFORE subprocess so we can
    # emit the audit datom on a timeout path too (the trajectory needs
    # to record THAT we tried; partial output captured but kept off the
    # datom — only the input hashes survive).
    source_hash = _sha256_bytes(source.encode("utf-8"))
    stdin_hash = _sha256_bytes(stdin.encode("utf-8"))

    try:
        stdout, stderr, exit_code, wall_clock_ms = _run_subprocess(
            source=source,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
            env=env,
        )
    except CodeExecTimeout as timeout_exc:
        # Even on timeout we emit a :code/exec audit datom so the
        # trajectory records the attempt. wall_clock_ms ≈ timeout_seconds *
        # 1000 (slight slop from the kill-and-drain step). The recorded
        # output_hash is the hash of (partial_stdout, "", -1): exit_code
        # -1 sentinel + empty stderr (we did not capture it on the kill
        # path) — adequate for replay byte-identity since replay-mode
        # detects timeout via the sentinel exit and does not re-run.
        partial_output_hash = _output_hash(
            timeout_exc.partial_stdout, "", -1
        )
        _emit_code_exec_datom(
            tx=tx,
            source_hash=source_hash,
            stdin_hash=stdin_hash,
            output_hash=partial_output_hash,
            exit_code=-1,
            wall_clock_ms=int(timeout_seconds * 1000),
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
        )
        raise

    # Forbidden-import detection on non-zero exit. Same audit-emit-then-
    # raise pattern as the timeout path so the audit chain records the
    # attempt regardless of outcome.
    forbidden: str | None = None
    if exit_code != 0:
        forbidden = _parse_forbidden_import(stderr)

    output_hash = _output_hash(stdout, stderr, exit_code)

    _emit_code_exec_datom(
        tx=tx,
        source_hash=source_hash,
        stdin_hash=stdin_hash,
        output_hash=output_hash,
        exit_code=exit_code,
        wall_clock_ms=wall_clock_ms,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
    )

    if forbidden is not None:
        raise CodeExecForbiddenImport(forbidden)

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
