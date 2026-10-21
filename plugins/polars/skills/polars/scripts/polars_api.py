#!/usr/bin/env python3
"""Look up the installed Polars API without a network or an MCP server.

Examples:
    python scripts/polars_api.py version
    python scripts/polars_api.py search join --limit 10
    python scripts/polars_api.py browse Expr.str
    python scripts/polars_api.py doc LazyFrame.join
    python scripts/polars_api.py doc pl.Expr.str.replace --json

The output always reflects the Polars package importable by this interpreter.
Use the project's interpreter (or `uv run`) so the version matches the code
being written. Exit status is 1 when a name cannot be resolved.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from dataclasses import dataclass, asdict

import polars as pl

NAMESPACES = ("str", "dt", "list", "struct", "cat", "name", "arr", "bin", "ext", "meta")
SCOPES = ("Expr", "DataFrame", "LazyFrame", "Series")


@dataclass
class Entry:
    name: str
    kind: str
    signature: str | None
    summary: str


def _public(name: str) -> bool:
    return not name.startswith("_")


def _signature(obj: object) -> str | None:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        if callable(obj) and not inspect.isclass(obj):
            try:
                return str(inspect.signature(obj.__call__))
            except (AttributeError, TypeError, ValueError):
                pass
        return None


def _summary(doc: str | None) -> str:
    if not doc:
        return ""
    for line in inspect.cleandoc(doc).splitlines():
        line = line.strip()
        if line and not line.startswith((">>>", "...")):
            return line
    return ""


def _namespace_class(prop: property) -> type | None:
    """Resolve `Expr.str`-style properties to the namespace class."""
    getter = prop.fget
    if getter is None:
        return None
    annotation = getter.__annotations__.get("return")
    if isinstance(annotation, str):
        return getter.__globals__.get(annotation)
    return annotation if isinstance(annotation, type) else None


def resolve(name: str) -> object:
    """Resolve `pl.X`, `X`, `Expr.str.method`, etc. against installed Polars."""
    parts = name.strip().split(".")
    if parts and parts[0] in {"pl", "polars"}:
        parts = parts[1:]
    if not parts:
        return pl

    obj: object = pl
    for part in parts:
        if isinstance(obj, type) and part in NAMESPACES:
            prop = inspect.getattr_static(obj, part)
            if isinstance(prop, property):
                nested = _namespace_class(prop)
                if nested is None:
                    raise AttributeError(f"{name}: cannot resolve namespace {part!r}")
                obj = nested
                continue
        obj = getattr(obj, part)
    return obj


def _class_entries(cls: type, prefix: str) -> list[Entry]:
    entries = []
    for attr in dir(cls):
        if not _public(attr):
            continue
        try:
            obj = getattr(cls, attr)
        except Exception:
            continue
        kind = "method" if callable(obj) else "attribute"
        entries.append(
            Entry(f"{prefix}.{attr}", kind, _signature(obj), _summary(getattr(obj, "__doc__", None)))
        )
    return entries


def collect_entries() -> list[Entry]:
    entries: list[Entry] = []
    for attr in dir(pl):
        if not _public(attr):
            continue
        obj = getattr(pl, attr)
        if inspect.ismodule(obj):
            continue
        kind = "class" if inspect.isclass(obj) else "function" if callable(obj) else "attribute"
        entries.append(
            Entry(f"pl.{attr}", kind, _signature(obj), _summary(getattr(obj, "__doc__", None)))
        )

    for scope in SCOPES:
        cls = getattr(pl, scope)
        entries.extend(_class_entries(cls, scope))
        if scope != "Expr":
            continue
        for ns in NAMESPACES:
            prop = inspect.getattr_static(cls, ns)
            if not isinstance(prop, property):
                continue
            nested = _namespace_class(prop)
            if nested is not None:
                entries.extend(_class_entries(nested, f"Expr.{ns}"))

    try:
        import polars.selectors as cs
    except ImportError:
        pass
    else:
        for attr in dir(cs):
            if _public(attr):
                obj = getattr(cs, attr)
                entries.append(
                    Entry(
                        f"selectors.{attr}",
                        "function" if callable(obj) else "attribute",
                        _signature(obj),
                        _summary(getattr(obj, "__doc__", None)),
                    )
                )
    return entries


def print_entry(entry: Entry, output: str) -> None:
    if output == "json":
        print(json.dumps(asdict(entry), indent=2))
        return
    signature = entry.signature or ""
    print(f"{entry.name}{signature}")
    if entry.summary:
        print(f"  {entry.summary}")


def command_version(args: argparse.Namespace) -> int:
    payload = {
        "polars": pl.__version__,
        "python": sys.version.split()[0],
        "executable": sys.executable,
    }
    if args.output == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(f"polars {payload['polars']}  (python {payload['python']})")
        print(payload["executable"])
    return 0


def command_search(args: argparse.Namespace) -> int:
    pattern = re.compile(re.escape(args.query), re.IGNORECASE)
    matches = [entry for entry in collect_entries() if pattern.search(entry.name)]
    matches.sort(key=lambda entry: (entry.name.count("."), len(entry.name), entry.name))
    matches = matches[: args.limit]
    if not matches:
        print(f"No API names matching {args.query!r} in polars {pl.__version__}", file=sys.stderr)
        return 1
    if args.output == "json":
        print(json.dumps([asdict(entry) for entry in matches], indent=2))
    else:
        for entry in matches:
            print_entry(entry, "text")
    return 0


def command_doc(args: argparse.Namespace) -> int:
    try:
        obj = resolve(args.name)
    except AttributeError as exc:
        print(f"{exc} (polars {pl.__version__})", file=sys.stderr)
        return 1
    entry = Entry(
        args.name,
        "class" if inspect.isclass(obj) else "callable" if callable(obj) else "attribute",
        _signature(obj),
        "",
    )
    if args.output == "json":
        payload = asdict(entry)
        payload["doc"] = inspect.getdoc(obj) or ""
        payload["polars"] = pl.__version__
        print(json.dumps(payload, indent=2))
    else:
        print(f"{entry.name}{entry.signature or ''}")
        doc = inspect.getdoc(obj)
        if doc:
            print()
            print(doc)
    return 0


def command_browse(args: argparse.Namespace) -> int:
    try:
        obj = resolve(args.name)
    except AttributeError as exc:
        print(f"{exc} (polars {pl.__version__})", file=sys.stderr)
        return 1
    if isinstance(obj, type):
        entries = _class_entries(obj, args.name)
    else:
        entries = []
        for attr in dir(obj):
            if not _public(attr):
                continue
            member = getattr(obj, attr)
            entries.append(
                Entry(
                    f"{args.name}.{attr}",
                    "callable" if callable(member) else "attribute",
                    _signature(member),
                    _summary(getattr(member, "__doc__", None)),
                )
            )
    entries = [entry for entry in entries if not args.filter or args.filter.lower() in entry.name.lower()]
    if args.output == "json":
        print(json.dumps([asdict(entry) for entry in entries], indent=2))
    else:
        for entry in entries:
            print_entry(entry, "text")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search or document the installed Polars API.",
        epilog="Run with the project's interpreter so the version matches.",
    )
    parser.add_argument("--output", choices=("text", "json"), default="text")
    sub = parser.add_subparsers(dest="command", required=True)

    version = sub.add_parser("version", help="show the resolved Polars version")
    version.set_defaults(func=command_version)

    search = sub.add_parser("search", help="find API names containing a term")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=command_search)

    doc = sub.add_parser("doc", help="show the signature and docstring for a name")
    doc.add_argument("name")
    doc.set_defaults(func=command_doc)

    browse = sub.add_parser("browse", help="list members of a class or namespace")
    browse.add_argument("name")
    browse.add_argument("--filter", help="only names containing this text")
    browse.set_defaults(func=command_browse)

    # Allow --output after the subcommand as well as before it.
    for command in (version, search, doc, browse):
        command.add_argument(
            "--output", choices=("text", "json"), default=argparse.SUPPRESS
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
