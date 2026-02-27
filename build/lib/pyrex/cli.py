"""
Pyrex CLI

Registered as the `pyrex` command via pyproject.toml [project.scripts].
After `pip install -e .` you can run:

    pyrex build app/page.pyx
    pyrex serve
    pyrex serve app/
    pyrex serve app/ --mode production
    pyrex serve app/ --port 8080
    pyrex serve app/ --mode production --port 8080 --env .env.staging
"""

import os
import signal
import sys
import webbrowser

# ── Argument parsing ──────────────────────────────────────────────────────────

_MODE_ALIASES = {
    "dev":        "development",
    "develop":    "development",
    "prod":       "production",
    "production": "production",
    "staging":    "staging",
}


def _parse_serve_args(rest: list) -> dict:
    """Parse positional and --flag arguments for `pyrex serve`."""
    cfg = {"directory": "app", "port": 3000, "mode": "development", "extra_env": None}
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("--"):
            key = arg[2:]
            val = rest[i + 1] if (i + 1 < len(rest) and not rest[i + 1].startswith("--")) else None
            if key in ("mode", "m") and val:
                cfg["mode"] = _MODE_ALIASES.get(val, val)
                i += 2
            elif key in ("port", "p") and val:
                cfg["port"] = int(val)
                i += 2
            elif key == "env" and val:
                cfg["extra_env"] = val
                i += 2
            else:
                i += 1
        else:
            # Positional: digit → port (backward compat), else → directory
            if arg.isdigit():
                cfg["port"] = int(arg)
            else:
                cfg["directory"] = arg
            i += 1
    return cfg


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        _print_usage()
        sys.exit(0)

    command = args[0]

    if command == "build":
        _cmd_build(args[1:])
    elif command == "serve":
        _cmd_serve(args[1:])
    elif command == "dev":
        _cmd_dev()
    else:
        print(f"Unknown command: '{command}'")
        print("Available commands: build, serve, dev")
        sys.exit(1)


# ── Commands ──────────────────────────────────────────────────────────────────

def _cmd_build(rest: list):
    from pyrex.engine import build_file

    if not rest:
        print("Usage: pyrex build <file.pyx>")
        sys.exit(1)
    filepath = rest[0]
    if not os.path.exists(filepath):
        print(f"Error: file not found: {filepath}")
        sys.exit(1)
    try:
        html = build_file(filepath)
        out = filepath.replace(".pyx", ".html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✓ Built → {out}  ({len(html)} bytes)")
    except Exception as e:
        print(f"✗ Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _cmd_serve(rest: list):
    import threading
    from pyrex.engine import serve
    from pyrex.env_loader import load_env_files

    cfg       = _parse_serve_args(rest)
    directory = cfg["directory"]
    port      = cfg["port"]
    mode      = cfg["mode"]
    extra_env = cfg["extra_env"]

    if not os.path.isdir(directory):
        print(f"Error: directory not found: {directory!r}")
        print("Create an app/ directory or pass a custom path: pyrex serve <dir>")
        sys.exit(1)

    # Load .env files before starting so all vars are in os.environ
    env_files = load_env_files(root_dir=".", mode=mode, extra=extra_env)

    # Try TUI; fall back to plain text if rich isn't installed
    try:
        from pyrex import tui as _tui
        _tui.console   # force import to catch missing rich early
    except Exception:
        _tui = None

    if _tui:
        try:
            from importlib.metadata import version as _pkg_ver
            ver = _pkg_ver("pyrex")
        except Exception:
            ver = "0.1.0"
        _tui.print_boot_sequence(ver)

    # Event that the 'r' key sets to request a manual full rebuild
    force_rebuild = threading.Event()

    def on_ready():
        if _tui:
            _tui.print_ready(
                host="localhost",
                port=port,
                env=mode,
                env_files=env_files,
            )
            _tui.start_key_listener(
                on_reload=lambda: force_rebuild.set(),
                on_quit=lambda: os.kill(os.getpid(), signal.SIGINT),
                on_open=lambda: webbrowser.open(f"http://localhost:{port}"),
            )
        else:
            print(f"\n  Pyrex dev server — {mode}")
            print(f"  http://localhost:{port}")
            if env_files:
                print(f"  env: {', '.join(env_files)}")
            print("  Ctrl+C to stop\n")

    serve(
        directory,
        port=port,
        mode=mode,
        on_ready=on_ready,
        force_rebuild=force_rebuild,
        env_files=env_files,
    )


def _cmd_dev():
    if not os.path.exists("main.py"):
        print("Error: no main.py found in current directory.")
        print("Create a main.py with: from pyrex import Pyrex; app = Pyrex(); app.run()")
        sys.exit(1)
    import subprocess
    subprocess.run([sys.executable, "main.py"])


# ── Help ──────────────────────────────────────────────────────────────────────

def _print_usage():
    print("Pyrex — Python JSX Framework\n")
    print("Usage:")
    print("  pyrex build <file.pyx>                  transpile to HTML")
    print("  pyrex serve [dir] [options]             start dev server\n")
    print("Serve options:")
    print("  --mode  dev|prod|staging                default: development")
    print("  --port  <number>                        default: 3000")
    print("  --env   <path>                          extra .env file to load\n")
    print("Examples:")
    print("  pyrex serve")
    print("  pyrex serve app/ --mode production --port 8080")
    print("  pyrex serve --mode staging --env .env.staging")


if __name__ == "__main__":
    main()
