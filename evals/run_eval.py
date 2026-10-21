#!/usr/bin/env python3
"""Grade an agent-written solution against the deterministic eval tasks.

This runner makes no model calls. Use it for a with-skill / without-skill
comparison by giving an agent the prompt and contract, saving its functions
to a module, then grading that module:

    python evals/run_eval.py --list
    python evals/run_eval.py --prompt top_n_per_group
    python evals/run_eval.py --solution path/to/solution.py
    python evals/run_eval.py --solution path/to/solution.py --task tag_counts
    python evals/run_eval.py --solution evals/reference_solution.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tasks  # noqa: E402


def load_solution(path: Path):
    spec = importlib.util.spec_from_file_location("candidate_solution", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_payload(task):
    payload = {}
    for name, frame in task.fixtures().items():
        schema = frame.collect_schema() if hasattr(frame, "collect_schema") else frame.schema
        rows = (frame.collect() if hasattr(frame, "collect") else frame).to_dicts()
        payload[name] = {
            "schema": {column: str(dtype) for column, dtype in schema.items()},
            "rows": rows,
        }
    return payload


def selected_tasks(values):
    if not values:
        return tasks.TASKS
    return [tasks.find(value) for value in values]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solution", type=Path, help="module containing the task functions")
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="task id to run; repeat for several (default: all)",
    )
    parser.add_argument("--list", action="store_true", help="list task ids")
    parser.add_argument("--prompt", help="print one task prompt and fixture schema")
    parser.add_argument("--fixture", help="print one task fixture as JSON")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)

    if args.list:
        for task in tasks.TASKS:
            print(f"{task.id}\t{task.prompt.splitlines()[0]}")
        return 0

    if args.prompt:
        task = tasks.find(args.prompt)
        print(task.prompt)
        print()
        print(task.contract)
        print()
        print("Fixture schema:")
        for name, frame in fixture_payload(task).items():
            print(f"  {name}: {frame['schema']}")
        return 0

    if args.fixture:
        print(json.dumps(fixture_payload(tasks.find(args.fixture)), indent=2))
        return 0

    if not args.solution:
        parser.error("--solution is required unless --list/--prompt/--fixture is used")
    if not args.solution.is_file():
        parser.error(f"solution not found: {args.solution}")

    try:
        module = load_solution(args.solution)
    except Exception as exc:
        print(f"cannot import solution: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    results = []
    for task in selected_tasks(args.tasks):
        try:
            task.check(module)
        except Exception as exc:
            results.append(
                {
                    "id": task.id,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            results.append({"id": task.id, "passed": True, "error": None})

    passed = sum(result["passed"] for result in results)
    if args.json:
        print(
            json.dumps(
                {
                    "solution": str(args.solution),
                    "passed": passed,
                    "total": len(results),
                    "tasks": results,
                },
                indent=2,
            )
        )
    else:
        for result in results:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status}\t{result['id']}")
            if result["error"]:
                print(f"     {result['error']}")
        print(f"\n{passed}/{len(results)} tasks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
