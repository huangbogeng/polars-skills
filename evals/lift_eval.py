#!/usr/bin/env python3
"""Measure task lift: does the skill improve answers on `tasks.py`?

Each run creates a temporary project, optionally installs the skill in the
agent's discovery path, asks the agent to write a solution module, and grades
the module with the same deterministic checks as `run_eval.py`. Running the
same tasks with and without the skill gives the lift directly.

    python evals/lift_eval.py --agent omp --runs 1                 # both arms
    python evals/lift_eval.py --agent claude,omp --runs 3 --json
    python evals/lift_eval.py --arm with --task top_n_per_group
    python evals/lift_eval.py --dry-run

This spends real model calls. It is not part of CI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_eval  # noqa: E402
import tasks  # noqa: E402
import trigger_eval  # noqa: E402

ARMS = ("without", "with")
SOLUTION = "solution.py"

INSTRUCTIONS = (
    "Save your answer as a Python module named {solution} in the current "
    "directory. {contract} Use only the installed polars package. Do not "
    "modify any other file."
)


def build_prompt(task) -> str:
    return f"{task.prompt}\n\n{INSTRUCTIONS.format(solution=SOLUTION, contract=task.contract)}"


def build_command(agent: str, prompt: str, model: str | None):
    """Headless command that may write files inside the working directory."""
    if agent == "claude":
        command = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools",
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Skill",
        ]
        if model:
            command.extend(["--model", model])
        return command
    if agent == "codex":
        # workspace-write lets the agent create solution.py; skip-git-repo-check
        # because the fixture directory is not a repository.
        command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    if agent == "omp":
        command = ["omp", "-p", "--mode", "json", "--no-session", "--no-title"]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    raise ValueError(f"unknown agent {agent!r}")


def install_skill(agent: str, root: Path, source: Path) -> Path | None:
    """Install the skill under its real name so implicit selection is measured."""
    dest = root / trigger_eval.AGENT_SKILL_ROOT[agent] / "polars"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def load_candidate(path: Path):
    """Import a candidate module, tolerating a top-level `main()` demo block.

    The stripped copy goes to a temporary directory so grading never writes
    next to the solution (which may be the checked-in reference solution).
    """
    source = path.read_text()
    source = re.sub(r"(?ms)^if __name__ == .__main__.:.*\Z", "", source)
    with tempfile.TemporaryDirectory(prefix="polars-candidate-") as temp:
        module_path = Path(temp) / (path.stem + "_candidate.py")
        module_path.write_text(source)
        spec = importlib.util.spec_from_file_location(
            "candidate_solution", module_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def grade(task, solution: Path) -> tuple[bool, str | None]:
    if not solution.is_file():
        return False, "solution.py was not created"
    try:
        module = load_candidate(solution)
        task.check(module)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
    return True, None


def run_one(agent: str, arm: str, task, source: Path, timeout: int, model: str | None):
    with tempfile.TemporaryDirectory(prefix=f"polars-lift-{arm}-") as temp:
        root = Path(temp)
        files = HERE / "files"
        if files.is_dir():
            for path in files.iterdir():
                if path.is_file():
                    shutil.copy2(path, root / path.name)
        if arm == "with":
            install_skill(agent, root, source)
        command = build_command(agent, build_prompt(task), model)
        code, output = trigger_eval.run_agent(command, root, timeout)
        passed, error = grade(task, root / SOLUTION)
        return {
            "agent": agent,
            "arm": arm,
            "task": task.id,
            "passed": passed,
            "error": error,
            "exit_code": code,
            "usage": trigger_eval.extract_usage(agent, output),
        }


def summarize(results):
    groups = {}
    for result in results:
        key = (result["agent"], result["arm"], result["task"])
        groups.setdefault(key, []).append(result)
    rows = []
    for (agent, arm, task), runs in sorted(groups.items()):
        passed = sum(run["passed"] for run in runs)
        tokens = [run["usage"]["total_tokens"] for run in runs]
        failures = [
            {
                "error": run["error"],
                "exit_code": run["exit_code"],
                "tokens": run["usage"]["total_tokens"],
            }
            for run in runs
            if not run["passed"]
        ]
        rows.append(
            {
                "agent": agent,
                "arm": arm,
                "task": task,
                "passed": passed,
                "runs": len(runs),
                "rate": passed / len(runs),
                "mean_tokens": sum(tokens) / len(tokens),
                "zero_token_runs": sum(
                    1 for token in tokens if token == 0
                ),
                "failures": failures,
            }
        )
    return rows


def lift_by_agent(rows):
    """Per-agent delta between arms, plus an overall row.

    A single-arm run has no lift to report: the missing arm is `None`, not 0,
    so it is never mistaken for a measured baseline.
    """
    agents = sorted({row["agent"] for row in rows})
    summary = []
    for agent in agents:
        summary.append(_lift_row(agent, [r for r in rows if r["agent"] == agent]))
    summary.append(_lift_row("all", rows))
    return summary


def _lift_row(agent, rows):
    with_rows = [r for r in rows if r["arm"] == "with"]
    without_rows = [r for r in rows if r["arm"] == "without"]

    def arm_rate(items):
        runs = sum(item["runs"] for item in items)
        return (sum(item["passed"] for item in items) / runs) if runs else None

    def arm_tokens(items):
        runs = sum(item["runs"] for item in items)
        if not runs:
            return None
        return round(sum(item["mean_tokens"] * item["runs"] for item in items) / runs)

    with_rate = arm_rate(with_rows)
    without_rate = arm_rate(without_rows)
    lift = None
    if with_rate is not None and without_rate is not None:
        lift = with_rate - without_rate
    with_tokens = arm_tokens(with_rows)
    without_tokens = arm_tokens(without_rows)
    token_delta = None
    if with_tokens is not None and without_tokens is not None:
        token_delta = with_tokens - without_tokens
    return {
        "agent": agent,
        "with_rate": with_rate,
        "without_rate": without_rate,
        "lift": lift,
        "with_tokens": with_tokens,
        "without_tokens": without_tokens,
        "token_delta": token_delta,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="omp", help="claude, codex, omp, or comma list")
    parser.add_argument("--arm", choices=("both",) + ARMS, default="both")
    parser.add_argument("--runs", type=int, default=1, help="runs per task per arm")
    parser.add_argument(
        "--task", action="append", dest="tasks", help="task id; repeat for several"
    )
    parser.add_argument("--timeout", type=int, default=300, help="seconds per run")
    parser.add_argument("--jobs", type=int, default=4, help="runs to run concurrently")
    parser.add_argument("--model", help="model override passed to each agent")
    parser.add_argument(
        "--skill-source",
        type=Path,
        default=trigger_eval.SKILL,
        help="skill directory to install (default: ./polars)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument("--dry-run", action="store_true", help="print commands only")
    args = parser.parse_args(argv)

    agents = [a.strip() for a in args.agent.split(",") if a.strip()]
    unknown = [a for a in agents if a not in trigger_eval.AGENT_SKILL_ROOT]
    if unknown:
        parser.error(f"unknown agent(s): {', '.join(unknown)}")
    arms = ARMS if args.arm == "both" else (args.arm,)
    selected = run_eval.selected_tasks(args.tasks)

    if args.dry_run:
        for agent in agents:
            for arm in arms:
                for task in selected:
                    print(f"{agent:6} {arm:8} {task.id}")
                    print("  " + " ".join(build_command(agent, build_prompt(task), args.model)))
        return 0

    jobs = [
        (agent, arm, task)
        for agent in agents
        for arm in arms
        for task in selected
        for _ in range(args.runs)
    ]
    results = []
    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    run_one, agent, arm, task, args.skill_source, args.timeout, args.model
                ): (agent, arm, task)
                for agent, arm, task in jobs
            }
            for future in as_completed(futures):
                results.append(future.result())
    else:
        for job in jobs:
            results.append(
                run_one(*job, args.skill_source, args.timeout, args.model)
            )

    rows = summarize(results)
    summary = lift_by_agent(rows)

    if args.json:
        print(json.dumps({"rows": rows, "summary": summary}, indent=2))
        return 0

    print(f"{'agent':6} {'arm':8} {'task':18} {'pass':>6} {'rate':>6} {'tokens':>9} note")
    for row in rows:
        notes = []
        if row["zero_token_runs"]:
            notes.append(f"{row['zero_token_runs']} run(s) reported no usage")
        for failure in row["failures"]:
            if failure["error"]:
                notes.append(failure["error"])
            elif failure["tokens"] == 0:
                notes.append(f"no output (exit {failure['exit_code']})")
        print(
            f"{row['agent']:6} {row['arm']:8} {row['task']:18} "
            f"{row['passed']}/{row['runs']:<4} {row['rate']:6.2f} {row['mean_tokens']:9.0f} "
            f"{'; '.join(notes[:2])}"
        )
    def cell(value, spec):
        if value is not None:
            return f"{value:{spec}}"
        width = spec.lstrip("+").split(".")[0]
        return f"{'-':>{width}}"

    print()
    print(f"{'agent':6} {'without':>8} {'with':>8} {'lift':>8} {'tokensΔ':>9}")
    for row in summary:
        print(
            f"{row['agent']:6} {cell(row['without_rate'], '8.2f')} "
            f"{cell(row['with_rate'], '8.2f')} {cell(row['lift'], '+8.2f')} "
            f"{cell(row['token_delta'], '+9.0f')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
