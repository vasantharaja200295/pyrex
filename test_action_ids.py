import hashlib
import os
import sys
from pathlib import Path

# Add the project root to sys.path
sys.path.append(os.path.abspath("."))

from pyrex.engine import _make_action_id, serve
from pyrex.parser.pyx_parser import parse_pyx_source

def test_action_id_uniqueness():
    print("Testing Action ID Uniqueness...")
    
    debug = False
    secret_key = "test_secret"
    
    # Check uniqueness across files
    id1 = _make_action_id("save", "/path/to/file1.pyx", secret_key, debug)
    id2 = _make_action_id("save", "/path/to/file2.pyx", secret_key, debug)
    
    print(f"Action 'save' in file1: {id1}")
    print(f"Action 'save' in file2: {id2}")
    
    if id1 != id2:
        print("PASS: IDs for the same name in different files are unique.")
    else:
        print("FAIL: IDs for the same name in different files collide.")

    # Check if it varies when secret is empty but csrf_token is different
    # This simulates the "auto-generated" secret requirement
    id4 = _make_action_id("save", "/path/to/file1.pyx", "csrf_1", debug)
    id5 = _make_action_id("save", "/path/to/file1.pyx", "csrf_2", debug)
    if id4 != id5:
        print("PASS: IDs vary with different auto-generated CSRF tokens.")
    else:
        print("FAIL: IDs do not vary with CSRF tokens when secret is empty.")

    # Check if it's the function name
    if id1 == "save":
        print("FAIL: ID is the function name in production mode.")
    else:
        print("PASS: ID is not the function name in production mode.")

if __name__ == "__main__":
    test_action_id_uniqueness()
