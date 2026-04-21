"""Wall-clock ban lint — paper §4.2 says non-determinism routes through
``:clock/now`` / ``:sys/random`` only (ARIS Round 1 R2 F5).

The banned call sites are:

- ``time.time(...)``
- ``datetime.now(...)``
- ``datetime.utcnow(...)``
- ``random.random(...)``, ``random.randint(...)``, ``random.choice(...)``,
  ``random.choices(...)``, ``random.uniform(...)``, ``random.randbytes(...)``,
  ``random.getrandbits(...)``
- ``uuid.uuid4(...)``

Exceptions (the authorized handler sources — the only places the real
system clock / rng / uuid may be sampled):

- ``src/persistence/effect/handlers/clock.py`` (the system-clock handler).
- ``src/persistence/effect/handlers/raw.py`` (the raw/echo handler,
  which by design samples ``random`` and is the target of mask/replay).
- ``src/persistence/effect/handlers/retry.py`` (jitter handler — wraps
  ``:sys/random`` but has an internal seam for testing).

The lint is a pure textual grep + minimal AST check (pathlib.Path.rglob
plus simple line matching). An AST would be more precise but this
catches the shapes we ban and is zero-maintenance as new modules land.

A file may opt out of one line by appending ``# noqa: wall-clock`` on
that line (e.g. a deliberate docstring example or a shim that has been
reviewed and justified). Comments alone are always skipped.
"""
from __future__ import annotations

import ast
import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "persistence"

# Fully-qualified call sites to ban. Keyed as (module, attr).
BANNED_CALLS: set[tuple[str, str]] = {
    ("time", "time"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("dt", "datetime"),  # covers `dt.datetime.now` via ast.Attribute chain below
    ("random", "random"),
    ("random", "randint"),
    ("random", "choice"),
    ("random", "choices"),
    ("random", "uniform"),
    ("random", "randbytes"),
    ("random", "getrandbits"),
    ("uuid", "uuid4"),
}

# Files that ARE allowed to call the banned primitives. These must be the
# authorized handler sources only.
ALLOWED_FILES: set[str] = {
    str(SRC_ROOT / "effect" / "handlers" / "clock.py"),
    str(SRC_ROOT / "effect" / "handlers" / "raw.py"),
    str(SRC_ROOT / "effect" / "handlers" / "retry.py"),
    # demo.py files are for humans running examples — skipped wholesale.
}


def _iter_source_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in SRC_ROOT.rglob("*.py"):
        # Demo files are excluded wholesale — they're human-facing examples,
        # not production code.
        if p.name == "demo.py":
            continue
        # Test files live under tests/, not src/, but be explicit.
        if "/tests/" in str(p):
            continue
        if str(p) in ALLOWED_FILES:
            continue
        out.append(p)
    return out


def _is_banned_call(node: ast.Call, source_lines: list[str]) -> str | None:
    """Return a human-readable violation description for this Call node,
    or None if it is not banned (or is opted out via noqa).
    """
    func = node.func
    # Pattern 1: `module.attr(...)`  → e.g. `random.random()`, `datetime.now()`
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        key = (func.value.id, func.attr)
        if key in BANNED_CALLS:
            # Check for noqa on the call's line.
            line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
            if "noqa: wall-clock" in line:
                return None
            return f"{func.value.id}.{func.attr}()"
    # Pattern 2: `a.b.c(...)` → e.g. `dt.datetime.now()`
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and isinstance(func.value.value, ast.Name)
    ):
        # Build chain repr for reporting.
        chain = f"{func.value.value.id}.{func.value.attr}.{func.attr}"
        # Check the last two parts against BANNED.
        key = (func.value.attr, func.attr)
        if key in BANNED_CALLS:
            line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
            if "noqa: wall-clock" in line:
                return None
            return f"{chain}()"
    return None


def test_no_wall_clock_calls_in_production_code():
    offences: list[str] = []
    for path in _iter_source_files():
        try:
            src = path.read_text()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        source_lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                desc = _is_banned_call(node, source_lines)
                if desc:
                    rel = path.relative_to(REPO_ROOT)
                    offences.append(f"{rel}:{node.lineno}  {desc}")

    if offences:
        msg = (
            "ARIS R2 F5 — wall-clock / rng / uuid calls in production code "
            "(paper §4.2 violation). Route through `:sys/now` / `:sys/random` "
            "effect handlers, or inject a clock/rng parameter. Allowed files: "
            f"{sorted({pathlib.Path(f).name for f in ALLOWED_FILES})}. "
            "If a violation is deliberate, append `# noqa: wall-clock` to its "
            "line. Violations:\n  " + "\n  ".join(sorted(offences))
        )
        raise AssertionError(msg)
