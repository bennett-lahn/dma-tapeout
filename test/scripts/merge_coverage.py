#!/usr/bin/env python3
"""Regenerate test/runs/m5_coverage_closure.json from retained fragments."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "test"
sys.path.insert(0, str(TEST))

from reference.coverage import find_fragments, regenerate_closure  # noqa: E402

def main() -> int:
    src = TEST / "runs"
    dest = src / "m5_coverage_closure.json"
    paths = find_fragments(str(src))
    print(f"fragments={len(paths)}")
    for path in paths:
        print(f"  {path}")
    report = regenerate_closure(str(src), str(dest))
    print(f"closed={report.closed}")
    print(f"depths={report.depths}")
    print(f"windows_counted={report.windows_counted}")
    print(f"windows_rejected={report.windows_rejected}")
    print("missing=")
    for cov_id, bins in sorted(report.missing.items()):
        print(f"  {cov_id}: {bins}")
    print(f"wrote={dest}")
    return 0 if report.closed else 1

if __name__ == "__main__":
    raise SystemExit(main())
