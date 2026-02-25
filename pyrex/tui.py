"""
Pyrex TUI — terminal output helpers for the dev server.

Requires: pip install rich
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.text import Text
from rich.rule import Rule

console = Console()

# ── Colour palette ────────────────────────────────────────────────────────────
C_BRAND          = "#4ADE80"
C_URL            = "#4ADE80"
C_ROUTE_OK       = "#86EFAC"
C_RELOAD         = "#FDE047"
C_METHOD_GET     = "#22C55E"
C_METHOD_POST    = "#3B82F6"
C_METHOD_PUT     = "#F59E0B"
C_METHOD_DELETE  = "#EF4444"
C_METHOD_PATCH   = "#A78BFA"
C_METHOD_HEAD    = "#94A3B8"
C_METHOD_OPTIONS = "#EC4899"
C_ACTION_BG      = "#0EA5E9"
C_STATUS_OK      = "#34D399"
C_STATUS_ERR     = "#F87171"
C_STATUS_RDR     = "#FBBF24"
C_ERROR_TEXT     = "#F87171"
C_MUTED          = "#6B7280"
C_DIM            = "#4B5563"
C_WHITE          = "#F1F5F9"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _method_badge(method: str) -> Text:
    m = method.upper()
    colours = {
        "GET":     (C_METHOD_GET,     "black"),
        "POST":    (C_METHOD_POST,    "white"),
        "PUT":     (C_METHOD_PUT,     "black"),
        "DELETE":  (C_METHOD_DELETE,  "white"),
        "PATCH":   (C_METHOD_PATCH,   "white"),
        "HEAD":    (C_METHOD_HEAD,    "black"),
        "OPTIONS": (C_METHOD_OPTIONS, "white"),
    }
    bg, fg = colours.get(m, ("#6B7280", "white"))
    padded = f" {m:<7}"
    return Text(padded, style=f"bold {fg} on {bg}")


def _status_badge(status: int) -> Text:
    if status < 300:
        bg, label = C_STATUS_OK,  f"✔ {status}"
    elif status < 400:
        bg, label = C_STATUS_RDR, f"→ {status}"
    else:
        bg, label = C_STATUS_ERR, f"✘ {status}"
    return Text(f" {label} ", style=f"bold white on {bg}")


# ── Boot ──────────────────────────────────────────────────────────────────────

def print_boot_sequence(version: str = "0.1.0"):
    console.print()
    title = Text()
    title.append("◈ PYREX ", style=f"bold {C_BRAND}")
    title.append(f"v{version}", style=C_MUTED)
    console.print(title)
    console.print()


def print_ready(
    host: str,
    port: int,
    env: str = "development",
    env_files: list = None,
):
    env_files = env_files or []

    url_line = Text()
    url_line.append("  ➜  Local     ", style=C_MUTED)
    url_line.append(f"http://{host}:{port}", style=f"bold underline {C_URL}")
    console.print(url_line)

    env_line = Text()
    env_line.append("     Env       ", style=C_MUTED)
    env_line.append(env, style=C_WHITE)
    if env_files:
        env_line.append("  (", style=C_MUTED)
        env_line.append(", ".join(env_files), style=C_MUTED)
        env_line.append(")", style=C_MUTED)
    console.print(env_line)

    console.print()
    console.print(Rule(style=C_DIM))

    keys = Text("  ")
    for key, desc in [("r", "reload"), ("o", "open in browser"), ("q", "quit")]:
        keys.append(key, style=f"bold {C_BRAND}")
        keys.append(f" {desc}   ", style=C_MUTED)
    console.print(keys)
    console.print()


# ── Live request log ──────────────────────────────────────────────────────────

def print_request(method: str, path: str, status: int, duration_ms: float):
    line = Text()
    line.append(f"  {_ts()}  ", style=C_MUTED)
    line.append_text(_method_badge(method))
    line.append(f"  {path:<30}", style=C_WHITE)
    line.append_text(_status_badge(status))
    line.append(f"  {duration_ms:.1f}ms", style=C_MUTED)
    console.print(line)


def print_action_call(name: str, duration_ms: float, ok: bool = True):
    icon   = "✔" if ok else "✘"
    colour = C_STATUS_OK if ok else C_STATUS_ERR
    line = Text()
    line.append(f"  {_ts()}  ", style=C_MUTED)
    line.append(" ACTION ", style=f"bold white on {C_ACTION_BG}")
    line.append(f"  {name:<30}", style=C_WHITE)
    line.append(f"{icon} ", style=f"bold {colour}")
    line.append(f"{duration_ms:.1f}ms", style=C_MUTED)
    console.print(line)


def print_error(message: str, traceback: Optional[str] = None):
    console.print()
    line = Text()
    line.append(f"  {_ts()}  ", style=C_MUTED)
    line.append(" ERROR ", style=f"bold white on {C_STATUS_ERR}")
    line.append(f"  {message}", style=C_ERROR_TEXT)
    console.print(line)
    if traceback:
        console.print(Text("  Traceback", style=f"bold {C_STATUS_ERR}"))
        for tb_line in traceback.strip().splitlines():
            console.print(Text(f"    {tb_line}", style=C_DIM))
    console.print()


# ── Reload ────────────────────────────────────────────────────────────────────

def print_reload_banner():
    line = Text()
    line.append("\n  ~ ", style=f"bold {C_RELOAD}")
    line.append("Reloading...\n", style=f"bold {C_RELOAD}")
    console.print(line)


def print_reload_done(routes: list):
    line = Text()
    line.append("  ✔ ", style=f"bold {C_STATUS_OK}")
    line.append("Ready   ", style=f"bold {C_STATUS_OK}")
    line.append("  ".join(routes), style=C_MUTED)
    console.print(line)
    console.print()


# ── Keyboard input ────────────────────────────────────────────────────────────

def _read_key_unix():
    import tty
    import termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def _read_key_windows():
    import msvcrt
    return msvcrt.getch().decode("utf-8", errors="ignore")


def read_key():
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_unix()


def start_key_listener(on_reload, on_quit, on_open=None):
    def _loop():
        while True:
            try:
                key = read_key()
                if key in ("r", "R"):
                    on_reload()
                elif key in ("o", "O") and on_open:
                    on_open()
                elif key in ("q", "Q", "\x03", "\x04"):
                    on_quit()
            except Exception:
                break
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
