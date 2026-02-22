#!/usr/bin/env python3
"""
pyrex build — transpile a .pyx file to HTML
pyrex serve — start dev server
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyrex.engine import build_file, serve


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python main.py build <file.pyx>")
        print("       python main.py serve <file.pyx> [port]")
        sys.exit(1)

    command = args[0]

    if command == "build":
        if len(args) < 2:
            print("Usage: python main.py build <file.pyx>")
            sys.exit(1)
        filepath = args[1]
        html = build_file(filepath)
        out = filepath.replace('.pyx', '.html')
        with open(out, 'w') as f:
            f.write(html)
        print(f"✓ Built → {out}")

    elif command == "serve":
        if len(args) < 2:
            print("Usage: python main.py serve <file.pyx> [port]")
            sys.exit(1)
        filepath = args[1]
        port = int(args[2]) if len(args) > 2 else 3000
        serve(filepath, port=port)

    elif command == "test":
        # Quick test - build and print
        filepath = args[1] if len(args) > 1 else "app/page.pyx"
        print(f"Building {filepath}...\n")
        try:
            html = build_file(filepath)
            print("✓ Build successful")
            print(f"  Output: {len(html)} chars, {html.count('<')} tags")
            # Save it
            out = filepath.replace('.pyx', '.html')
            with open(out, 'w') as f:
                f.write(html)
            print(f"  Saved: {out}")
        except Exception as e:
            import traceback
            print(f"✗ Build failed: {e}")
            traceback.print_exc()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
