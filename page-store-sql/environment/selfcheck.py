#!/usr/bin/env python3

import os
import subprocess
import sys
import tempfile

PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
ENGINE = os.environ.get("PAGESTORE_BIN", "/app/pagestore.py")


def main() -> int:
    names = sorted(n[:-4] for n in os.listdir(PUBLIC) if n.endswith(".sql"))
    passed = 0
    for name in names:
        sql_path = os.path.join(PUBLIC, name + ".sql")
        out_path = os.path.join(PUBLIC, name + ".out")
        expected = open(out_path, encoding="utf-8").read()
        with tempfile.TemporaryDirectory() as db:
            proc = subprocess.run(
                [sys.executable, ENGINE, "--db", db, "--file", sql_path],
                capture_output=True,
                text=True,
            )
        if proc.returncode == 0 and proc.stdout == expected:
            passed += 1
        else:
            sys.stderr.write(f"FAIL {name}\n")
    print(f"{passed}/{len(names)}")
    return 0 if passed == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
