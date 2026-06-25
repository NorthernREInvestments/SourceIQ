#!/usr/bin/env python3
"""End-to-end two-stage test runner."""

import io
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

RUNS = [
    ("Stage 1: pet supplies", ["python", "run.py", "pet supplies", "--scrape-only", "--no-open"]),
    ("Stage 2: dog collar drill-down", [
        "python", "run.py", "pet supplies", "--drill-down", "dog collar",
        "--scrape-only", "--no-open",
    ]),
    ("Stage 1: home decor", ["python", "run.py", "home decor", "--scrape-only", "--no-open"]),
    ("Stage 1: baby products", ["python", "run.py", "baby products", "--scrape-only", "--no-open"]),
    ("Stage 1: car accessories", ["python", "run.py", "car accessories", "--scrape-only", "--no-open"]),
]

LOG = "output/e2e_test.log"


def main():
    with open(LOG, "w", encoding="utf-8") as log:
        for title, cmd in RUNS:
            header = f"\n{'=' * 60}\n{title}\n{'=' * 60}\n"
            print(header)
            log.write(header)
            log.flush()
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = result.stdout + result.stderr
            print(out)
            log.write(out)
            log.flush()
            if result.returncode != 0:
                print(f"FAILED: {title} (exit {result.returncode})", file=sys.stderr)
                return result.returncode

        credit_path = "output/credit_log.txt"
        try:
            with open(credit_path, encoding="utf-8") as cf:
                credit = cf.read()
        except OSError:
            credit = "(credit log not found)"
        footer = f"\n{'=' * 60}\nFULL CREDIT LOG: {credit_path}\n{'=' * 60}\n{credit}"
        print(footer)
        log.write(footer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
