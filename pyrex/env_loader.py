"""
Pyrex environment file loader.

Load order (lowest → highest priority; each file can override earlier ones):
  1. .env
  2. .env.{mode}           e.g. .env.development  or  .env.production
  3. .env.local            local overrides — never commit this file
  4. .env.{mode}.local     e.g. .env.development.local

Process environment variables are NEVER overwritten (OS always wins).
Files that don't exist are silently skipped.

Usage:
    from pyrex.env_loader import load_env_files
    loaded = load_env_files(root_dir=".", mode="development")
    # loaded = [".env", ".env.development", ".env.local"]
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env_files(
    root_dir: str = ".",
    mode: str = "development",
    extra: str | None = None,
) -> list[str]:
    """
    Load .env files for the given mode into os.environ.

    Returns the list of filenames that were actually found and applied.
    Pass extra= to also load an additional env file at the given path.
    """
    root = Path(root_dir).resolve()
    candidates: list[Path] = [
        root / ".env",
        root / f".env.{mode}",
        root / ".env.local",
        root / f".env.{mode}.local",
    ]
    if extra:
        candidates.append(Path(extra).resolve())

    loaded: list[str] = []
    for env_file in candidates:
        if env_file.is_file():
            _apply_env_file(env_file)
            loaded.append(env_file.name)
    return loaded


# ── Internal helpers ──────────────────────────────────────────────────────────

def _apply_env_file(path: Path) -> None:
    """Parse path and set vars that are not already in os.environ."""
    for key, value in _parse_env_file(path).items():
        if key not in os.environ:
            os.environ[key] = value


def _parse_env_file(path: Path) -> dict[str, str]:
    """Return all key=value pairs from a .env file."""
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Skip blanks and full-line comments
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result[key] = _parse_value(raw_value)
    return result


def _parse_value(raw: str) -> str:
    """Strip surrounding quotes and optional trailing inline comment."""
    v = raw.strip()
    # Double-quoted: "hello world"  — spaces preserved, no inline comments
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    # Single-quoted: 'hello world'  — literal, no escaping
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    # Unquoted: strip inline comment (first " #" sequence)
    if " #" in v:
        v = v[: v.index(" #")]
    return v.strip()
