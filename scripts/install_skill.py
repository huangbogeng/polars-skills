#!/usr/bin/env python3
"""Install the canonical `polars/` skill for Claude Code, Codex, and omp.

Layouts served:
    Claude Code  ~/.claude/skills/polars       <project>/.claude/skills/polars
    Codex        ~/.agents/skills/polars        <project>/.agents/skills/polars
    omp          ~/.agents/skills/polars        <project>/.agents/skills/polars

Codex and omp share `.agents/skills`, so selecting both writes one copy.
Symlinks are the default so edits in this repository are picked up without
reinstalling. Use --copy for an isolated snapshot.

Examples:
    python scripts/install_skill.py --agents all
    python scripts/install_skill.py --agents claude --scope project
    python scripts/install_skill.py --agents codex,omp --copy --force
    python scripts/install_skill.py --agents all --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "polars"

# Codex and omp both discover `.agents/skills/<name>/SKILL.md`.
TARGETS = {
    "claude": {
        "user": Path(".claude") / "skills" / "polars",
        "project": Path(".claude") / "skills" / "polars",
    },
    "codex": {
        "user": Path(".agents") / "skills" / "polars",
        "project": Path(".agents") / "skills" / "polars",
    },
    "omp": {
        "user": Path(".agents") / "skills" / "polars",
        "project": Path(".agents") / "skills" / "polars",
    },
}


def destinations(agents, scopes, home: Path, project: Path):
    result = []
    seen = set()
    for agent in agents:
        for scope in scopes:
            root = home if scope == "user" else project
            dest = root / TARGETS[agent][scope]
            key = str(dest.absolute())
            if key in seen:
                continue
            seen.add(key)
            result.append((agent, scope, dest))
    return result


def install(dest: Path, *, copy: bool, force: bool) -> str:
    if dest.is_symlink():
        if dest.resolve() == SOURCE.resolve():
            return "already linked"
        if not force:
            raise FileExistsError(f"{dest} links elsewhere; use --force")
        dest.unlink()
    elif dest.exists():
        if not force:
            raise FileExistsError(f"{dest} exists; use --force")
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copytree(
            SOURCE,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return "copied"
    try:
        dest.symlink_to(SOURCE, target_is_directory=True)
        return "linked"
    except OSError:
        shutil.copytree(
            SOURCE,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return "copied (symlink unavailable)"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agents",
        default="all",
        help="comma-separated: claude,codex,omp,all (default: all)",
    )
    parser.add_argument(
        "--scope",
        default="user",
        choices=("user", "project", "both"),
        help="where to install (default: user)",
    )
    parser.add_argument("--copy", action="store_true", help="copy instead of symlink")
    parser.add_argument("--force", action="store_true", help="replace an existing install")
    parser.add_argument("--dry-run", action="store_true", help="show actions without writing")
    parser.add_argument("--home", type=Path, default=Path.home(), help="user home override")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project root override")
    args = parser.parse_args(argv)

    requested = args.agents.strip().lower()
    agents = (
        list(TARGETS) if requested == "all"
        else [agent.strip() for agent in requested.split(",") if agent.strip()]
    )
    unknown = [agent for agent in agents if agent not in TARGETS]
    if unknown:
        parser.error(f"unknown agent(s): {', '.join(unknown)}")
    if not SOURCE.is_dir():
        parser.error(f"source skill not found: {SOURCE}")

    scopes = ("user", "project") if args.scope == "both" else (args.scope,)
    failed = False
    for agent, scope, dest in destinations(agents, scopes, args.home, args.project):
        label = f"{agent:6} {scope:7} {dest}"
        if args.dry_run:
            action = "copy" if args.copy else "link"
            print(f"[dry-run] {label} -> {action}")
            continue
        try:
            action = install(dest, copy=args.copy, force=args.force)
        except FileExistsError as exc:
            failed = True
            print(f"[skip]    {label}: {exc}", file=sys.stderr)
        else:
            print(f"[{action:8}] {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
