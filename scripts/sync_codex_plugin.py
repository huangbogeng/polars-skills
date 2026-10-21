#!/usr/bin/env python3
"""Vendor the canonical skill into the Codex plugin layout.

Codex copies a plugin into its cache and does not follow symlinks, so the
marketplace plugin needs real files. `polars/` remains the single source of
truth; run this script after changing it.

    python scripts/sync_codex_plugin.py
    python scripts/sync_codex_plugin.py --check
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "polars"
DEST = ROOT / "plugins" / "polars" / "skills" / "polars"
PARTS = ("SKILL.md", "agents", "references", "scripts")


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def compare() -> list[str]:
    problems = []
    expected = set()
    for part in PARTS:
        source = SOURCE / part
        if not source.exists():
            problems.append(f"missing source: {source.relative_to(ROOT)}")
            continue
        if source.is_file():
            expected.add(Path(part))
        else:
            for path in iter_files(source):
                expected.add(path.relative_to(SOURCE))
    actual = {path.relative_to(DEST) for path in iter_files(DEST)} if DEST.exists() else set()
    for relative in sorted(expected - actual):
        problems.append(f"missing: {relative}")
    for relative in sorted(actual - expected):
        problems.append(f"unexpected: {relative}")
    for relative in sorted(expected & actual):
        left = SOURCE / relative
        right = DEST / relative
        if not filecmp.cmp(left, right, shallow=False):
            problems.append(f"differs: {relative}")
    return problems


def sync() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for part in PARTS:
        source = SOURCE / part
        target = DEST / part
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        elif source.is_file():
            shutil.copy2(source, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args(argv)

    if args.check:
        problems = compare()
        if problems:
            print("Codex plugin copy is out of date:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            print("Run: python scripts/sync_codex_plugin.py", file=sys.stderr)
            return 1
        print(f"Codex plugin copy is current ({DEST.relative_to(ROOT)})")
        return 0

    sync()
    print(f"Updated {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
