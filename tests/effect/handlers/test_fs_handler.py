"""Phase 2.2a G1 — :fs/* handler unit coverage."""
from __future__ import annotations
import base64
import hashlib
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from persistence.effect.handlers.fs import (
    FsCapabilityDenied, make_fs_handler,
)
from persistence.effect.runtime import Runtime, with_runtime


@pytest.fixture
def fs_runtime(tmp_path: Path) -> tuple[Path, Path, Runtime]:
    project_root = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_root.mkdir()
    scratch_dir.mkdir()
    handler = make_fs_handler(project_root=project_root, scratch_dir=scratch_dir)
    return project_root, scratch_dir, Runtime(handlers=[handler])


def test_fs_read_text_happy_path(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    f = project_root / "input.txt"
    f.write_text("hello\nworld\n")
    with with_runtime(rt) as r:
        result = r.perform(":fs/read", {"path": str(f)})
    assert result["bytes_or_text"] == "hello\nworld\n"
    assert result["size"] == 12
    assert result["sha256"] == hashlib.sha256(b"hello\nworld\n").hexdigest()
    assert isinstance(result["mtime"], float)


def test_fs_read_capability_denial_outside_project_root(fs_runtime, tmp_path):
    project_root, scratch_dir, rt = fs_runtime
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("nope")
    with with_runtime(rt) as r, pytest.raises(FsCapabilityDenied) as exc:
        r.perform(":fs/read", {"path": str(outside)})
    assert "outside project_root" in str(exc.value)


def test_fs_read_missing_file(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    with with_runtime(rt) as r, pytest.raises(FileNotFoundError):
        r.perform(":fs/read", {"path": str(project_root / "missing.txt")})


def test_fs_read_binary_base64_round_trip(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    raw = bytes(range(256))
    f = project_root / "blob.bin"
    f.write_bytes(raw)
    with with_runtime(rt) as r:
        result = r.perform(":fs/read", {"path": str(f), "encoding": "binary"})
    decoded = base64.b64decode(result["bytes_or_text"])
    assert decoded == raw
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()


def test_fs_write_text_happy_path(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    target = scratch_dir / "out.txt"
    with with_runtime(rt) as r:
        result = r.perform(":fs/write", {
            "path": str(target),
            "bytes_or_text": "summary text",
        })
    assert target.read_text() == "summary text"
    assert result["bytes_written"] == 12
    assert result["sha256_after"] == hashlib.sha256(b"summary text").hexdigest()


def test_fs_write_capability_denial_outside_scratch(fs_runtime, tmp_path):
    project_root, scratch_dir, rt = fs_runtime
    outside = tmp_path / "leak.txt"
    with with_runtime(rt) as r, pytest.raises(FsCapabilityDenied):
        r.perform(":fs/write", {"path": str(outside), "bytes_or_text": "x"})


def test_fs_glob_canonical_sorted(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    for name in ["b.py", "a.py", "c.py"]:
        (project_root / name).write_text("# stub")
    with with_runtime(rt) as r:
        result = r.perform(":fs/glob", {
            "pattern": "*.py",
            "root": str(project_root),
            "flags": {"recursive": False},
        })
    assert result["matches"] == sorted(result["matches"]), "matches must be canonical-sorted"
    assert len(result["matches"]) == 3


def test_fs_grep_canonical_sorted(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    (project_root / "a.txt").write_text("foo\nbar\nfoo\n")
    (project_root / "b.txt").write_text("foo\n")
    with with_runtime(rt) as r:
        result = r.perform(":fs/grep", {
            "pattern": "foo",
            "root": str(project_root),
            "flags": {},
        })
    keys = [(m["path"], m["line"]) for m in result["matches"]]
    assert keys == sorted(keys), "grep matches must be canonical-sorted"
    assert len(result["matches"]) == 3  # a.txt:1, a.txt:3, b.txt:1


def test_fs_grep_empty_canonicalized(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    (project_root / "a.txt").write_text("nothing relevant\n")
    with with_runtime(rt) as r:
        result = r.perform(":fs/grep", {
            "pattern": "needle",
            "root": str(project_root),
            "flags": {},
        })
    assert result == {"matches": []}


@given(content=st.text(min_size=0, max_size=200))
@settings(max_examples=100, deadline=None)
def test_fs_round_trip_property(tmp_path_factory, content):
    """Hypothesis: write a string, read it back, get byte-identity."""
    tmp = tmp_path_factory.mktemp("rt")
    project_root = tmp / "p"; scratch_dir = tmp / "s"
    project_root.mkdir(); scratch_dir.mkdir()
    handler = make_fs_handler(project_root=project_root, scratch_dir=scratch_dir)
    rt = Runtime(handlers=[handler])
    target = scratch_dir / "round.txt"
    with with_runtime(rt) as r:
        r.perform(":fs/write", {"path": str(target), "bytes_or_text": content})
        result = r.perform(":fs/read", {"path": str(target)})
    assert result["bytes_or_text"] == content


# Symlink escape: a symlink under project_root pointing outside MUST resolve to
# the real path; if real path is outside project_root, deny.
def test_fs_read_symlink_escape_denied(fs_runtime, tmp_path):
    project_root, scratch_dir, rt = fs_runtime
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    bad_link = project_root / "link_to_secret"
    bad_link.symlink_to(outside)
    with with_runtime(rt) as r, pytest.raises(FsCapabilityDenied):
        r.perform(":fs/read", {"path": str(bad_link)})


def test_fs_write_mode_append(fs_runtime):
    project_root, scratch_dir, rt = fs_runtime
    target = scratch_dir / "log.txt"
    target.write_text("first\n")
    with with_runtime(rt) as r:
        r.perform(":fs/write", {
            "path": str(target),
            "bytes_or_text": "second\n",
            "mode": "a",
        })
    assert target.read_text() == "first\nsecond\n"
