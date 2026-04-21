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
    # ARIS Round 4 — ``time.monotonic`` and ``time.perf_counter`` are
    # wall-clock-ish: they affect elapsed_ms / latency measurements on
    # AuditEntry and the §6.3 regulator-replay p95 target. Route
    # through ``:clock/now`` instead. Verified zero hits in src/
    # before banning.
    ("time", "monotonic"),
    ("time", "perf_counter"),
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


def _has_noqa_in_span(node: ast.Call, source_lines: list[str]) -> bool:
    """Return True if any physical line of the call's span carries
    ``noqa: wall-clock``. Covers the R4 R2-new-G2 multi-line case — a
    call split across lines with the noqa on the closing paren line
    should be accepted.
    """
    start = max(node.lineno - 1, 0)
    # ``end_lineno`` is 1-based and inclusive; default to start if absent
    # (older Python AST — not applicable on 3.10+, but guard anyway).
    end = getattr(node, "end_lineno", None)
    end_idx = (end - 1) if end is not None else start
    end_idx = min(end_idx, len(source_lines) - 1)
    for i in range(start, end_idx + 1):
        if "noqa: wall-clock" in source_lines[i]:
            return True
    return False


def _is_banned_call(node: ast.Call, source_lines: list[str]) -> str | None:
    """Return a human-readable violation description for this Call node,
    or None if it is not banned (or is opted out via noqa).
    """
    func = node.func
    # Pattern 1: `module.attr(...)`  → e.g. `random.random()`, `datetime.now()`
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        key = (func.value.id, func.attr)
        if key in BANNED_CALLS:
            if _has_noqa_in_span(node, source_lines):
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
            if _has_noqa_in_span(node, source_lines):
                return None
            return f"{chain}()"
    return None


def _scan_source_for_violations(src: str) -> list[str]:
    """Run the lint's AST walker on a string and return a list of
    ``"<desc>"`` violation strings. Factored out so the plant-and-catch
    self-test below can exercise the detector on a planted violation
    without polluting the real tree (ARIS Round 3 P-rigor-polish G1).
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    source_lines = src.splitlines()
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            desc = _is_banned_call(node, source_lines)
            if desc:
                out.append(f"line {node.lineno}: {desc}")
    return out


def test_no_wall_clock_calls_in_production_code():
    offences: list[str] = []
    for path in _iter_source_files():
        try:
            src = path.read_text()
        except OSError:
            continue
        source_lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
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


# ---------------------------------------------------------------------------
# Plant-and-catch self-test for the lint itself (ARIS Round 3 G1).
#
# Before this, the lint passed as long as production code was clean —
# but a regression that silently broke the detector (e.g. refactoring
# _is_banned_call to always return None) would make every future
# violation pass unnoticed. This self-test plants each banned call
# pattern and confirms the detector flags it.
# ---------------------------------------------------------------------------


def test_lint_detects_planted_datetime_now():
    planted = "import datetime\n" "datetime.now()\n"
    violations = _scan_source_for_violations(planted)
    assert violations, (
        "lint failed to flag a planted datetime.now() — detector regression"
    )
    assert any("datetime.now" in v for v in violations), violations


def test_lint_detects_planted_time_time():
    planted = "import time\n" "time.time()\n"
    violations = _scan_source_for_violations(planted)
    assert any("time.time" in v for v in violations), violations


def test_lint_detects_planted_time_monotonic():
    """ARIS Round 4 — ``time.monotonic()`` is wall-clock-ish (affects
    latency measurements / elapsed_ms on AuditEntry). Must be banned.
    """
    planted = "import time\n" "time.monotonic()\n"
    violations = _scan_source_for_violations(planted)
    assert any("time.monotonic" in v for v in violations), violations


def test_lint_detects_planted_time_perf_counter():
    """ARIS Round 4 — ``time.perf_counter()`` is wall-clock-ish (same
    latency-measurement vector as monotonic). Must be banned.
    """
    planted = "import time\n" "time.perf_counter()\n"
    violations = _scan_source_for_violations(planted)
    assert any("time.perf_counter" in v for v in violations), violations


def test_lint_detects_planted_random_random():
    planted = "import random\n" "random.random()\n"
    violations = _scan_source_for_violations(planted)
    assert any("random.random" in v for v in violations), violations


def test_lint_detects_planted_uuid_uuid4():
    planted = "import uuid\n" "uuid.uuid4()\n"
    violations = _scan_source_for_violations(planted)
    assert any("uuid.uuid4" in v for v in violations), violations


def test_lint_detects_planted_chained_dt_datetime_now():
    """``dt.datetime.now()`` via the a.b.c chain."""
    planted = "import datetime as dt\n" "dt.datetime.now()\n"
    violations = _scan_source_for_violations(planted)
    assert any("datetime.now" in v for v in violations), violations


def test_lint_does_not_flag_noqa_annotated_call():
    planted = (
        "import datetime\n"
        "datetime.now()  # noqa: wall-clock -- deliberate for docstring\n"
    )
    violations = _scan_source_for_violations(planted)
    assert not violations, f"noqa annotation ignored: {violations}"


def test_lint_does_not_flag_noqa_on_multiline_call():
    """ARIS Round 4 R2 new G2 — the noqa scan must cover the whole
    span of a multi-line call (from ``lineno`` through ``end_lineno``),
    not only the line where the call starts. A long ``datetime.now(...)``
    split across lines with the noqa on the closing paren line should
    not be flagged.
    """
    planted = (
        "import datetime\n"
        "datetime.now(\n"
        "    datetime.timezone.utc,\n"
        ")  # noqa: wall-clock -- deliberate example\n"
    )
    violations = _scan_source_for_violations(planted)
    assert not violations, (
        f"multi-line noqa ignored — detector only reads lineno line: {violations}"
    )


def test_lint_plant_in_tempfile_gets_flagged(tmp_path):
    """End-to-end plant: write a real .py file with a planted
    ``datetime.now()`` and assert the shared detector would flag it.
    """
    planted_file = tmp_path / "planted.py"
    planted_file.write_text(
        "import datetime\n"
        "def stamp():\n"
        "    return datetime.now()\n"
    )
    src = planted_file.read_text()
    violations = _scan_source_for_violations(src)
    assert violations, (
        f"planted file {planted_file} produced no violations — detector broken"
    )
