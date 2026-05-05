"""Phase 2.2a — :fs/* effect handlers (read, write, glob, grep)."""
from __future__ import annotations
import base64
import hashlib
import re
from pathlib import Path
from typing import Any

from persistence.effect.runtime import Handler


class FsCapabilityDenied(Exception):
    """Raised when path resolves outside the configured project_root or scratch_dir."""


_VALID_WRITE_MODES: tuple[str, ...] = ("w", "wb", "a", "ab")


def _safe_resolve(p: str | Path, *allowed_roots: Path) -> Path:
    """Resolve `p` strict=False (path may not exist for writes); deny if real path
    is not relative to ANY of `allowed_roots`. Symlink-following is intentional."""
    resolved = Path(p).resolve(strict=False)
    if not any(resolved.is_relative_to(root.resolve()) for root in allowed_roots):
        raise FsCapabilityDenied(
            f"path {p!r} resolves to {resolved} which is outside allowed roots: "
            f"{[str(r.resolve()) for r in allowed_roots]}"
        )
    return resolved


def _read_clause(project_root: Path, scratch_dir: Path) -> Any:
    def _clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        path = _safe_resolve(args["path"], project_root, scratch_dir)
        encoding = args.get("encoding", "utf-8")
        if encoding == "binary":
            data = path.read_bytes()
            return {
                "bytes_or_text": base64.b64encode(data).decode("ascii"),
                "mtime": float(path.stat().st_mtime),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        # Use open() with newline="" to disable universal newline translation
        # (Path.read_text's newline= param only exists in Python ≥3.13).
        with path.open(encoding=encoding, newline="") as fh:
            text = fh.read()
        raw = text.encode(encoding)
        return {
            "bytes_or_text": text,
            "mtime": float(path.stat().st_mtime),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return _clause


def _write_clause(scratch_dir: Path) -> Any:
    def _clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        path = _safe_resolve(args["path"], scratch_dir)
        content = args["bytes_or_text"]
        mode = args.get("mode", "w")
        if mode not in _VALID_WRITE_MODES:
            raise ValueError(
                f":fs/write mode must be one of {_VALID_WRITE_MODES!r}, got {mode!r}"
            )
        if mode in ("wb", "ab"):
            data = base64.b64decode(content)
            with path.open(mode) as f:
                bytes_written = f.write(data)
        else:
            data = content.encode("utf-8")
            with path.open(mode, encoding="utf-8", newline="") as f:
                f.write(content)
                bytes_written = len(data)
        # sha256_after computed over the FULL file post-write
        full = path.read_bytes()
        return {
            "bytes_written": bytes_written,
            "sha256_after": hashlib.sha256(full).hexdigest(),
        }
    return _clause


def _glob_clause(project_root: Path, scratch_dir: Path) -> Any:
    def _clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        root = _safe_resolve(args["root"], project_root, scratch_dir)
        pattern = args["pattern"]
        flags = args.get("flags", {})
        if flags.get("recursive", True):
            matches = [str(p) for p in root.rglob(pattern) if p.is_file()]
        else:
            matches = [str(p) for p in root.glob(pattern) if p.is_file()]
        return {"matches": sorted(matches)}
    return _clause


def _grep_clause(project_root: Path, scratch_dir: Path) -> Any:
    def _clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        root = _safe_resolve(args["root"], project_root, scratch_dir)
        pattern = args["pattern"]
        flags = args.get("flags", {})
        re_flags = re.IGNORECASE if flags.get("ignore_case") else 0
        compiled = re.compile(
            re.escape(pattern) if flags.get("fixed_string") else pattern,
            re_flags,
        )
        results: list[dict[str, Any]] = []
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            try:
                for lineno, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if compiled.search(line):
                        results.append({"path": str(f), "line": lineno, "text": line})
            except (PermissionError, IsADirectoryError):
                continue
        results.sort(key=lambda r: (r["path"], r["line"]))
        return {"matches": results}
    return _clause


def make_fs_handler(
    *,
    project_root: Path,
    scratch_dir: Path,
    name: str = "fs",
) -> Handler:
    """Factory for the :fs/* handler quartet.

    LD6 — capability constraints baked in at construction time.
    Reads are confined to project_root; writes are confined to scratch_dir.
    """
    return Handler(
        name=name,
        wraps={":fs/read", ":fs/write", ":fs/glob", ":fs/grep"},
        clauses={
            ":fs/read": _read_clause(project_root, scratch_dir),
            ":fs/write": _write_clause(scratch_dir),
            ":fs/glob": _glob_clause(project_root, scratch_dir),
            ":fs/grep": _grep_clause(project_root, scratch_dir),
        },
    )
