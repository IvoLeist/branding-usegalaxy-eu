#!/usr/bin/env python3
"""Report line coverage for factsheet/update_factsheet.py using stdlib trace."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "factsheet" / "update_factsheet.py"
TEST_MODULE = "tests/test_update_factsheet.py"
COVER_FILE = "factsheet.update_factsheet.cover"


def parse_cover_file(path: Path) -> tuple[set[int], set[int], dict[int, str]]:
    covered = set()
    missing = set()
    source = {}
    line_number = 0
    for line in path.read_text().splitlines():
        line_number += 1
        prefix = line[:7]
        text = line[7:] if len(line) >= 7 else ""
        source[line_number] = text

        if prefix.startswith(">>>>>>"):
            missing.add(line_number)
            continue

        match = re.match(r"\s*\d+:", prefix)
        if match:
            covered.add(line_number)

    return covered, missing, source


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


def run_trace(coverdir: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "trace",
        "--count",
        "--missing",
        "--coverdir",
        str(coverdir),
        "--module",
        "unittest",
        TEST_MODULE,
    ]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--show-covered",
        action="store_true",
        help="Print the covered line ranges as well as missing line ranges.",
    )
    parser.add_argument(
        "--coverdir",
        type=Path,
        help="Optional directory where raw .cover files should be kept.",
    )
    args = parser.parse_args()

    if args.coverdir:
        coverdir = args.coverdir
        if coverdir.exists():
            shutil.rmtree(coverdir)
        coverdir.mkdir(parents=True)
        run_trace(coverdir)
        cover_file = coverdir / COVER_FILE
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            coverdir = Path(tmpdir)
            run_trace(coverdir)
            cover_file = coverdir / COVER_FILE
            covered, missing, source = parse_cover_file(cover_file)
            print_report(covered, missing, source, args.show_covered, None)
            return 0

    covered, missing, source = parse_cover_file(cover_file)
    print_report(covered, missing, source, args.show_covered, cover_file)
    return 0


def print_report(
    covered: set[int],
    missing: set[int],
    source: dict[int, str],
    show_covered: bool,
    cover_file: Path | None,
) -> None:
    executable = covered | missing
    percent = 100.0 * len(covered) / len(executable) if executable else 100.0
    print(f"{TARGET.relative_to(ROOT)} line coverage: {len(covered)}/{len(executable)} ({percent:.1f}%)")
    if cover_file:
        print(f"annotated trace file: {cover_file}")
    if show_covered:
        print(f"covered lines: {compress_ranges(covered)}")
    print(f"missing lines: {compress_ranges(missing)}")
    if missing:
        print("\nMissing source lines:")
        for line in sorted(missing):
            print(f"{line:4}: {source[line]}")


if __name__ == "__main__":
    raise SystemExit(main())
