"""
Pyrex CLI

Registered as the `pyrex` command via pyproject.toml [project.scripts].
After `pip install -e .` you can run:

    pyrex build app/page.pyx
    pyrex serve app/page.pyx
    pyrex serve app/page.pyx 8080
"""

import sys
import os
from pyrex.engine import build_file, serve


def main():
    args = sys.argv[1:]

    if not args:
        print("Pyrex - Python JSX Framework")
        print("")
        print("Usage:")
        print("  pyrex build <file.pyx>           transpile to HTML")
        print("  pyrex serve <file.pyx> [port]    start dev server (default port 3000)")
        sys.exit(0)

    command = args[0]

    if command == "build":
        if len(args) < 2:
            print("Usage: pyrex build <file.pyx>")
            sys.exit(1)
        filepath = args[1]
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

    elif command == "serve":
        if len(args) < 2:
            print("Usage: pyrex serve <file.pyx> [port]")
            sys.exit(1)
        filepath = args[1]
        if not os.path.exists(filepath):
            print(f"Error: file not found: {filepath}")
            sys.exit(1)
        port = int(args[2]) if len(args) > 2 else 3000
        serve(filepath, port=port)

    else:
        print(f"Unknown command: '{command}'")
        print("Available commands: build, serve")
        sys.exit(1)


if __name__ == "__main__":
    main()