"""
Pyrex CLI

Registered as the `pyrex` command via pyproject.toml [project.scripts].
After `pip install -e .` you can run:

    pyrex build app/page.pyx           transpile a single file to HTML
    pyrex serve app/                   start dev server (scans for page.pyx files)
    pyrex serve app/ 8080              on a custom port
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
        print("  pyrex build <file.pyx>          transpile a single file to HTML")
        print("  pyrex serve <directory> [port]  start dev server (default port 3000)")
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
        # Directory is optional — defaults to app/
        # Accepted forms: pyrex serve  |  pyrex serve 8080  |  pyrex serve myapp/  |  pyrex serve myapp/ 8080
        directory = "app"
        port = 3000
        rest = args[1:]
        if rest:
            if rest[0].isdigit():
                port = int(rest[0])
            else:
                directory = rest[0]
                if len(rest) > 1:
                    port = int(rest[1])
        if not os.path.isdir(directory):
            print(f"Error: directory not found: {directory!r}")
            print("Create an app/ directory or pass a custom path: pyrex serve <dir>")
            sys.exit(1)
        serve(directory, port=port)

    elif command == "dev":
        # Run main.py in the current directory (Pyrex bootstrap entry point)
        if not os.path.exists("main.py"):
            print("Error: no main.py found in current directory.")
            print("Create a main.py with: from pyrex import Pyrex; app = Pyrex(); app.run()")
            sys.exit(1)
        import subprocess
        subprocess.run([sys.executable, "main.py"])

    else:
        print(f"Unknown command: '{command}'")
        print("Available commands: build, serve, dev")
        sys.exit(1)


if __name__ == "__main__":
    main()