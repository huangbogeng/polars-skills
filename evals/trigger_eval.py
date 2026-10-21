#!/usr/bin/env python3
"""Measure whether Claude Code, Codex, and omp load the skill.

Each run creates a temporary project with the skill installed in that agent's
discovery path, sends one prompt in headless mode, and inspects tool events
for a read of the skill. No prompt is copied into the skill, so a model that
answers from general knowledge without loading the skill counts as "not
triggered".

Examples:
    python evals/trigger_eval.py --dry-run
    python evals/trigger_eval.py --agent claude
    python evals/trigger_eval.py --agent codex,omp --runs 3
    python evals/trigger_eval.py --agent all --json

This spends real model calls. It is not part of CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "polars"
TRIGGERS = Path(__file__).with_name("triggers.json")
PROBE = "polars-probe"

AGENT_SKILL_ROOT = {
    "claude": Path(".claude") / "skills",
    "codex": Path(".agents") / "skills",
    "omp": Path(".agents") / "skills",
}


def load_cases(split):
    data = json.loads(TRIGGERS.read_text())
    cases = []
    if split in ("all", "should_fire"):
        cases.extend(("should_fire", item) for item in data["should_fire"])
    if split in ("all", "should_not_fire"):
        cases.extend(("should_not_fire", item) for item in data["should_not_fire"])
    return data["skill"], cases


def build_command(agent, prompt, model):
    if agent == "claude":
        command = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools",
            "Read",
            "Skill",
        ]
        if model:
            command.extend(["--model", model])
        return command
    if agent == "codex":
        command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
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


def prepare_project(agent, root: Path, source: Path = SKILL) -> Path:
    """Copy the skill under a unique name so a user-level `polars` cannot win.

    Prompt fixtures are copied into the project so data questions have data.
    """
    files = Path(__file__).with_name("files")
    if files.is_dir():
        for path in files.iterdir():
            if path.is_file():
                shutil.copy2(path, root / path.name)
    dest = root / AGENT_SKILL_ROOT[agent] / PROBE
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    skill_md = dest / "SKILL.md"
    text = skill_md.read_text()
    text = re.sub(r"(?m)^name: polars\s*$", f"name: {PROBE}", text, count=1)
    skill_md.write_text(text)
    return dest


def run_pty(command, cwd: Path, timeout: int) -> tuple[int, str]:
    """Run with a PTY; Codex emits no JSON when stdout is a plain pipe."""
    import pty

    master, slave = pty.openpty()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    chunks: list[bytes] = []
    deadline = time.time() + timeout
    try:
        while True:
            if time.time() > deadline:
                process.kill()
                chunks.append(b"\n[trigger-eval timeout]\n")
                break
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            elif process.poll() is not None:
                try:
                    while True:
                        data = os.read(master, 65536)
                        if not data:
                            break
                        chunks.append(data)
                except OSError:
                    pass
                break
    finally:
        os.close(master)
        process.wait()
    return process.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def run_plain(command, cwd: Path, timeout: int) -> tuple[int, str]:
    result = subprocess.run(
        command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout + result.stderr


def run_agent(command, cwd: Path, timeout: int) -> tuple[int, str]:
    if os.name == "posix":
        return run_pty(command, cwd, timeout)
    return run_plain(command, cwd, timeout)


def parse_events(output: str):
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def triggered(agent: str, output: str, skill: str, skill_dir: Path) -> bool:
    """Return True only when a tool actually reads the skill.

    Path mentions in directory listings or prompt context do not count.
    """
    events = list(parse_events(output))
    needle = f"skill://{skill}"
    skill_path = str(skill_dir / "SKILL.md")

    if agent == "claude":
        for event in events:
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") not in ("Skill", "Read"):
                    continue
                if skill in json.dumps(block.get("input", {})):
                    return True
        return False

    if agent == "codex":
        for event in events:
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "command_execution":
                command = item.get("command", "")
                if skill_path in command or (
                    "SKILL.md" in command and skill in command
                ):
                    return True
            elif item.get("type") == "mcp_tool_call":
                blob = json.dumps(item.get("arguments", {}))
                if skill_path in blob or needle in blob:
                    return True
        return False

    if agent == "omp":
        for event in events:
            calls = []
            if event.get("type") == "tool_execution_start":
                calls.append((event.get("toolName"), event.get("args", {})))
            elif event.get("type") == "toolcall_end":
                call = event.get("toolCall") or {}
                calls.append((call.get("name"), call.get("arguments", {})))
            for name, arguments in calls:
                blob = json.dumps(arguments)
                if name == "read" and (needle in blob or skill_path in blob):
                    return True
                if name == "bash" and "SKILL.md" in blob and skill in blob:
                    return True
        return False

    return False


def extract_usage(agent: str, output: str) -> dict:
    """Return normalized token and cost totals from each agent's final event."""
    events = list(parse_events(output))
    if agent == "claude":
        for event in reversed(events):
            if event.get("type") != "result":
                continue
            usage = event.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cost = event.get("total_cost_usd")
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
            }
    elif agent == "codex":
        for event in reversed(events):
            if event.get("type") != "turn.completed":
                continue
            usage = event.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": None,
            }
    elif agent == "omp":
        for event in reversed(events):
            if event.get("type") != "agent_end":
                continue
            input_tokens = output_tokens = total_tokens = 0
            cost = 0.0
            saw_cost = False
            for message in event.get("messages", []):
                if message.get("role") != "assistant":
                    continue
                usage = message.get("usage") or {}
                input_tokens += int(usage.get("input") or 0)
                output_tokens += int(usage.get("output") or 0)
                total_tokens += int(
                    usage.get("totalTokens")
                    or (usage.get("input") or 0) + (usage.get("output") or 0)
                )
                if isinstance(usage.get("cost"), (int, float)):
                    cost += float(usage["cost"])
                    saw_cost = True
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost if saw_cost else None,
            }
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": None,
    }


def run_case(agent, split, case, skill, timeout, model, source):
    with tempfile.TemporaryDirectory(prefix="polars-trigger-") as temp:
        root = Path(temp)
        skill_dir = prepare_project(agent, root, source)
        command = build_command(agent, case["prompt"], model)
        code, output = run_agent(command, root, timeout)
        fired = triggered(agent, output, skill, skill_dir)
        passed = fired if split == "should_fire" else not fired
        return {
            "agent": agent,
            "split": split,
            "case": case["id"],
            "expected": split == "should_fire",
            "triggered": fired,
            "passed": passed,
            "exit_code": code,
            "usage": extract_usage(agent, output),
            "output_tail": output[-2000:],
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="all", help="claude, codex, omp, or all")
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default: 3)")
    parser.add_argument(
        "--fire-threshold",
        type=float,
        default=0.5,
        help="minimum trigger rate for should_fire cases (default: 0.5)",
    )
    parser.add_argument(
        "--quiet-threshold",
        type=float,
        default=0.2,
        help="maximum trigger rate for should_not_fire cases (default: 0.2)",
    )
    parser.add_argument("--timeout", type=int, default=180, help="seconds per run")
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="cases to run concurrently (default: 4; use 1 to serialize)",
    )
    parser.add_argument("--model", help="model override passed to each agent")
    parser.add_argument(
        "--skill-source",
        type=Path,
        default=SKILL,
        help="skill directory to test (default: ./polars)",
    )
    parser.add_argument(
        "--split",
        choices=("all", "should_fire", "should_not_fire"),
        default="all",
    )
    parser.add_argument("--case", help="only run this case id")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument("--dry-run", action="store_true", help="print commands only")
    args = parser.parse_args(argv)

    agents = list(AGENT_SKILL_ROOT) if args.agent == "all" else [
        agent.strip() for agent in args.agent.split(",") if agent.strip()
    ]
    unknown = [agent for agent in agents if agent not in AGENT_SKILL_ROOT]
    if unknown:
        parser.error(f"unknown agent(s): {', '.join(unknown)}")
    _, cases = load_cases(args.split)
    skill = PROBE
    if args.case:
        cases = [case for case in cases if case[1]["id"] == args.case]
        if not cases:
            parser.error(f"unknown case id: {args.case}")

    if args.dry_run:
        for agent in agents:
            for split, case in cases:
                command = build_command(agent, case["prompt"], args.model)
                print(f"{agent:6} {split:15} {case['id']}")
                print("  " + " ".join(repr(part) if " " in part else part for part in command))
        return 0

    jobs = [
        (agent, split, case)
        for agent in agents
        for split, case in cases
        for _ in range(args.runs)
    ]

    def execute(job):
        agent, split, case = job
        return run_case(
            agent,
            split,
            case,
            skill,
            args.timeout,
            args.model,
            args.skill_source,
        )

    if args.jobs > 1 and len(jobs) > 1:
        runs = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(execute, job): index
                for index, job in enumerate(jobs)
            }
            for future in as_completed(futures):
                runs[futures[future]] = future.result()
    else:
        runs = [execute(job) for job in jobs]

    grouped = {}
    for result in runs:
        key = (result["agent"], result["split"], result["case"])
        group = grouped.setdefault(
            key,
            {
                "agent": result["agent"],
                "split": result["split"],
                "case": result["case"],
                "runs": 0,
                "fired": 0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
                "results": [],
            },
        )
        group["runs"] += 1
        group["fired"] += int(result["triggered"])
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            group["usage"][key] += result["usage"][key]
        if result["usage"]["cost_usd"] is not None:
            group["usage"]["cost_usd"] += result["usage"]["cost_usd"]
        group["results"].append(result)

    for group in grouped.values():
        group["rate"] = group["fired"] / group["runs"]
        limit = args.fire_threshold if group["split"] == "should_fire" else args.quiet_threshold
        group["passed"] = (
            group["rate"] >= limit
            if group["split"] == "should_fire"
            else group["rate"] <= limit
        )

    passed = sum(group["passed"] for group in grouped.values())
    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "total": len(grouped),
                    "runs": len(runs),
                    "thresholds": {
                        "fire": args.fire_threshold,
                        "quiet": args.quiet_threshold,
                    },
                    "cases": list(grouped.values()),
                },
                indent=2,
            )
        )
    else:
        for group in grouped.values():
            status = "PASS" if group["passed"] else "FAIL"
            expected = "fire" if group["split"] == "should_fire" else "no-fire"
            tokens = group["usage"]["total_tokens"]
            cost = group["usage"]["cost_usd"]
            cost_text = f" ${cost:.4f}" if cost else ""
            print(
                f"{status} {group['agent']:6} {expected:7} "
                f"{group['fired']}/{group['runs']} "
                f"({group['rate']:.0%}) {group['case']} "
                f"tok={tokens}{cost_text}"
            )
        print(f"\n{passed}/{len(grouped)} cases matched expectation")
    return 0 if passed == len(grouped) else 1


if __name__ == "__main__":
    raise SystemExit(main())
