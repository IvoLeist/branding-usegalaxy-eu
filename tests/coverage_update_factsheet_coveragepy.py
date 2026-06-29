#!/usr/bin/env python3
"""Report coverage for factsheet/update_factsheet.py using coverage.py."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "factsheet" / "update_factsheet.py"
TEST_FILE = ROOT / "tests" / "test_update_factsheet.py"


def compress_ranges(lines: set[int]) -> str:
    if not lines:
        return "-"
    ranges = []
    sorted_lines = sorted(lines)
    start = previous = sorted_lines[0]
    for line in sorted_lines[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append((start, previous))
        start = previous = line
    ranges.append((start, previous))
    return ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges)


def load_coverage_module():
    try:
        import coverage
    except ImportError:
        print(
            "coverage.py is not installed. Install it with: python3 -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return coverage


def run_tests_under_coverage(coverage_module):
    cov = coverage_module.Coverage(source=[str(TARGET.parent)], branch=True)
    cov.start()
    suite = unittest.defaultTestLoader.discover(str(TEST_FILE.parent), pattern=TEST_FILE.name)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    cov.stop()
    cov.save()
    return cov, result


def report_target_coverage(cov, *, show_covered: bool, html_dir: Path | None, xml_path: Path | None) -> int:
    _filename, statements, _excluded, missing, _missing_formatted = cov.analysis2(str(TARGET))
    statements = set(statements)
    missing = set(missing)
    covered = statements - missing
    percent = 100.0 * len(covered) / len(statements) if statements else 100.0

    print(f"{TARGET.relative_to(ROOT)} line coverage: {len(covered)}/{len(statements)} ({percent:.1f}%)")
    if show_covered:
        print(f"covered lines: {compress_ranges(covered)}")
    print(f"missing lines: {compress_ranges(missing)}")

    if missing:
        source = TARGET.read_text().splitlines()
        print("\nMissing source lines:")
        for line in sorted(missing):
            print(f"{line:4}: {source[line - 1]}")

    if html_dir:
        cov.html_report(directory=str(html_dir), include=[str(TARGET)])
        print(f"html report: {html_dir / 'index.html'}")
    if xml_path:
        cov.xml_report(outfile=str(xml_path), include=[str(TARGET)])
        print(f"xml report: {xml_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--show-covered",
        action="store_true",
        help="Print the covered line ranges as well as missing line ranges.",
    )
    parser.add_argument("--html-dir", type=Path, help="Optional directory for a coverage.py HTML report.")
    parser.add_argument("--xml", type=Path, help="Optional path for a coverage.py XML report.")
    args = parser.parse_args()

    coverage_module = load_coverage_module()
    cov, result = run_tests_under_coverage(coverage_module)
    report_target_coverage(cov, show_covered=args.show_covered, html_dir=args.html_dir, xml_path=args.xml)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
