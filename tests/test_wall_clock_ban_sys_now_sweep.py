"""Phase 2.4b LD-4 G3 — AST ban scan over the 5 migrated provenance sites.

After 2.4b's LD-4 sweep, none of these modules may call
``dt.datetime.now(...)``, ``datetime.now(...)``, ``dt.datetime.utcnow(...)``,
or ``datetime.utcnow(...)`` AT THE 5 MIGRATED PROVENANCE SITES. The only
acceptable substrate-time read for those sites is
``substrate.effect.perform(":sys/now", {})``.

R0-fold I2 added _searcher.py to the sweep (5 sites total after the fold;
the original design had 4). R0-fold N1 widened the matcher from a narrow
``dt.datetime.now(`` pattern to ``.now()`` AND ``.utcnow()`` across alias
forms (``dt`` and ``datetime`` leftmost names).

**``# noqa: wall-clock`` opt-out** is honored on a per-line basis (mirror
of the convention from ``tests/test_wall_clock_ban.py:89-104``). Sites
beyond the 5 LD-4 migrations within these modules — latency-measurement
reads (``_session.py:223,226`` for ``:act/result`` latency_ms, paired
with the ``started_at_ms`` int-ms W3 rescope), session-start timestamps
(``_session.py:76`` ``_session_start_dt``) — carry explicit
``# noqa: wall-clock`` annotations and are NOT part of the LD-4 sweep.
Their W3 disposition is documented at site (latency-tracking gated by
the site-6 int-ms representation contract; G5 xfail-strict marker at
``tests/coder/test_mcts_started_at_ms_sys_now.py`` per T6).

``_searcher.py:591`` (``started_at_ms`` via ``time.time_ns()``) is
``time.time_ns()`` — orthogonal to the ``.now()`` / ``.utcnow()`` AST
matcher and exempted intrinsically.

Falsifiability: if any of the 5 LD-4 migrations is forgotten — or a
future PR re-introduces a ``dt.datetime.now()`` / ``datetime.utcnow()``
read at one of those sites without a ``# noqa: wall-clock`` annotation —
the AST scan trips with the offender's relpath + line + matched
attribute name.
"""
from __future__ import annotations

import ast
import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

MIGRATED_FILES = (
    "src/persistence/http/routes/blob.py",
    "src/persistence/http/routes/claim.py",
    "src/persistence/coder/_session.py",
    "src/persistence/coder/_planner.py",
    "src/persistence/coder/_searcher.py",
)

BANNED_ATTRS = {"now", "utcnow"}
BANNED_LEFTMOST = {"dt", "datetime"}


def _has_noqa_in_span(node: ast.Call, source_lines: list[str]) -> bool:
    """Return True if any physical line of the call's span carries
    ``noqa: wall-clock``. Mirror of
    ``tests/test_wall_clock_ban.py:89-104`` for cross-test consistency.
    Covers multi-line calls (e.g. arg split across lines with the
    noqa on the closing paren line).
    """
    start = max(node.lineno - 1, 0)
    end = getattr(node, "end_lineno", None)
    end_idx = (end - 1) if end is not None else start
    end_idx = min(end_idx, len(source_lines) - 1)
    for i in range(start, end_idx + 1):
        if "noqa: wall-clock" in source_lines[i]:
            return True
    return False


def test_no_wall_clock_in_migrated_provenance_sites() -> None:
    """LD-4 G3 — no ``datetime.now()`` / ``.utcnow()`` in the 5 migrated modules.

    Entity-id sites (``uuid.uuid4().hex``) are orthogonal to the AST
    matcher (the ban targets ``.now()`` / ``.utcnow()`` only).
    ``_searcher.py:591``'s ``time.time_ns() // 1_000_000`` for
    ``started_at_ms`` is also orthogonal (the matcher does not look at
    ``time.time_ns``); its W3 rescope is the G5 xfail-strict marker
    landing in T6.

    Per-line ``# noqa: wall-clock`` opt-outs ARE honored — see module
    docstring for the rationale (latency measurements and session-start
    timestamps in these modules are deliberate non-migrations gated by
    the same site-6 int-ms representation contract).
    """
    # Sanity check the paths resolve so a future repo restructure doesn't
    # silently make the scan a no-op.
    for relpath in MIGRATED_FILES:
        src_path = REPO_ROOT / relpath
        assert src_path.is_file(), (
            f"G3 scan target missing: {relpath} (resolved to {src_path}). "
            "Update MIGRATED_FILES if a module was moved."
        )

    offenders: list[str] = []
    for relpath in MIGRATED_FILES:
        src_path = REPO_ROOT / relpath
        src = src_path.read_text()
        source_lines = src.splitlines()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr in BANNED_ATTRS):
                continue
            # Walk the attribute chain down to the leftmost Name.
            leftmost = func.value
            while isinstance(leftmost, ast.Attribute):
                leftmost = leftmost.value
            leftmost_name = (
                leftmost.id if isinstance(leftmost, ast.Name) else ""
            )
            if leftmost_name not in BANNED_LEFTMOST:
                continue
            # Honor per-line ``# noqa: wall-clock`` opt-outs (mirror of
            # tests/test_wall_clock_ban.py convention).
            if _has_noqa_in_span(node, source_lines):
                continue
            offenders.append(
                f"{relpath}:{node.lineno}: "
                f"{leftmost_name}.{func.attr}() call — must route "
                f"through substrate.effect.perform(':sys/now', {{}})"
            )

    assert not offenders, (
        "wall-clock reads found in migrated provenance modules — "
        "Phase 2.4b LD-4 requires substrate.effect.perform(':sys/now', {})"
        " (or an explicit `# noqa: wall-clock` annotation per the existing"
        " test_wall_clock_ban.py precedent):\n"
        + "\n".join("  " + o for o in offenders)
    )
