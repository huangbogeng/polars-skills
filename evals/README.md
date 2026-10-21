# Skill evaluation

Deterministic task success evaluation for the Polars skill. The runner makes
no model calls: an agent writes a solution module, and the runner grades it
against exact expected DataFrames.

This is the piece the repository was missing. Reference links and syntax
tests keep the documentation honest; this harness measures whether an agent
can actually use the skill to produce correct Polars code.

## Protocol

Run each task twice with the same model, temperature, and prompt:

1. `with_skill` — the agent has access to `polars/`.
2. `without_skill` — the same agent, no skill.

For every task:

```bash
python evals/run_eval.py --list
python evals/run_eval.py --prompt top_n_per_group
```

Give the agent the prompt and contract, plus the fixture schema if useful:

```bash
python evals/run_eval.py --fixture top_n_per_group
```

Save its functions to a solution module, then grade:

```bash
python evals/run_eval.py --solution path/to/solution.py --json
```

Compare the two arms by pass rate and by failure mode. A skill that improves
pass rate but adds tool calls or tokens still needs a cost check.

## Lift harness

`lift_eval.py` automates that protocol. Each run creates a temporary project,
copies `evals/files/*`, optionally installs `polars/` in the agent's
discovery path (the `with` arm), asks the agent to write `solution.py`, and
grades it with the same checks as `run_eval.py`. The summary reports pass
rate per arm and the lift.

```bash
python evals/lift_eval.py --agent omp --runs 1 --jobs 4
python evals/lift_eval.py --agent omp --arm with --task scale_numeric
python evals/lift_eval.py --agent codex,omp --runs 3 --json
```

An arm that was not run reports `-`, never `0.00`, so a missing baseline
cannot be mistaken for a measured one. One run per task is a smoke check;
use `--runs 3` or more before quoting a lift. Claude Code reports
`total_cost_usd`, omp reports per-message cost, and Codex reports tokens only.
The harness makes real model calls and is not part of CI.

### Recorded runs

Each entry is the full command that produced it. More runs supersede fewer.

| Date | Agent | Tasks × runs | without | with | lift | tokens Δ |
|---|---|---|---|---|---|---|
| 2026-10-21 | codex | 5 × 1 | 0.80 | 0.80 | 0.00 | −10,079 |
| 2026-10-21 | omp | 5 × 1 | 0.60 | 0.60 | 0.00 | −8,846 |

Commands: `lift_eval.py --agent <agent> --runs 1 --jobs 4 --timeout 240 --json`.

A longer omp rerun (`--jobs 2 --timeout 420`) reproduced 0.60 in both arms.

Pass/fail per task was identical in both arms:

| Task | Codex | omp | Failure |
|---|---|---|---|
| `top_n_per_group` | pass | pass | — |
| `tag_counts` | pass | pass | — |
| `scale_numeric` | pass | pass | — |
| `validate_join` | fail | fail | codex calls `LazyFrame.get_column`; omp returns a `ValueError`-raising check in the `with` arm and a `LazyFrame` with no `.height` in the `without` arm |
| `calendar_growth` | pass | fail | omp returns mismatched dtypes in both arms; the 240 s batch also timed out twice |

`join_check` now accepts any exception for the duplicate-key case: the
contract says "must raise", `validate="m:1"` raises `ComputeError`, and a
hand-written uniqueness check raises `ValueError`. Fan-out still fails.

One run per task cannot resolve a lift smaller than one task, and the token
deltas are within run-to-run noise, so these numbers show **no measured
lift**, not a measured absence of lift. All five tasks are ones a strong
model can solve unaided; a ceiling effect is the most likely explanation and
the fix is harder tasks, not more runs.

Add a row only after the run finishes; leave the cell as `-` when that arm
was not measured.

## Tasks

| Task | What it checks |
|---|---|
| `top_n_per_group` | deterministic ties, sort-before-group, `maintain_order` |
| `calendar_growth` | calendar gaps, zero denominators, null semantics |
| `validate_join` | dimension-table cardinality via `validate="m:1"` |
| `tag_counts` | empty vs null lists, `explode`, row counts |
| `scale_numeric` | expression expansion and column order |

All tasks run on Polars 1.44.2 and 2.0.0.

## Trigger evaluation

`trigger_eval.py` measures whether Claude Code, Codex, and omp load the skill.
It creates a temporary project, copies `evals/files/*` into it, installs a
uniquely named copy (`polars-probe`, same description) in that agent's
discovery path, sends a prompt headlessly, and checks tool events for an
actual read. A directory listing or a mention in the prompt does not count.
The fixture data matters: without an input file, an implicit data question
can be dismissed before any skill is considered.

```bash
python evals/trigger_eval.py --dry-run
python evals/trigger_eval.py --agent claude --runs 3
python evals/trigger_eval.py --agent codex,omp --runs 3
python evals/trigger_eval.py --agent all --json
```

The probe name avoids a user-level `polars` installation winning the
name-resolution race. Each run spends a real model call, so this is not part
of CI. Results report the trigger rate per case:

- `should_fire` passes at `--fire-threshold` (default 0.5).
- `should_not_fire` passes at or below `--quiet-threshold` (default 0.2).

Single runs are noisy. Every case needs at least 10 runs per agent before a
result is used to compare descriptions: at 3 runs, 0/3 and 1/3 differ by one
sample and cannot be distinguished from noise (the 95% interval for 3 trials
spans most of [0, 1]). Report the rate as `fired/runs` and only call a
difference real when it survives 10+ runs per case, preferably on more than
one agent.

### Trigger cases

`triggers.json` holds 10 `should_fire` and 11 `should_not_fire` cases. Each
prompt is a headless-executable task; `evals/files/*` (`sales.csv`,
`orders.csv`, `events.ndjson`) are copied into the temporary project first.

| Case | Split | Intent |
|---|---|---|
| `explicit-polars` | fire | explicit Polars request on a CSV |
| `implicit-default` | fire | Python table analysis with no library named — Polars is the default |
| `pandas-migration` | fire | user asks for a pandas→Polars conversion |
| `debug-existing-polars` | fire | existing Polars code raises an error; needs skill API knowledge (`.agg`, sort after agg) |
| `optimize-polars-pipeline` | fire | Polars eager script too slow; lazy pipeline rewrite |
| `parquet-partitioned-scan` | fire | partitioned Parquet source with pushdown requirements |
| `timeseries-rolling` | fire | time-series rolling window with null warm-up semantics |
| `join-cardinality-check` | fire | join fan-out diagnosis; `validate="m:1"` territory |
| `dataframe-no-library` | fire | "dataframe" named, no library — DataFrames idiom question |
| `ndjson-timeseries-gap` | fire | NDJSON source plus time bucketing with missing minutes |
| `explicit-pandas` | no-fire | user explicitly chose pandas |
| `explicit-sql` | no-fire | SQL query itself, not Python |
| `explicit-duckdb` | no-fire | user explicitly chose duckdb |
| `explicit-pyspark` | no-fire | user explicitly chose PySpark / cluster |
| `concept-no-code` | no-fire | conceptual question, no code task |
| `unrelated` | no-fire | Rust language question |
| `web-frontend` | no-fire | React component; renders props, no data work |
| `polars-cloud-excluded` | no-fire | distributed/cluster Polars, excluded by the skill |
| `polars-gpu-excluded` | no-fire | GPU Polars, excluded by the skill |
| `pandas-stays-pandas` | no-fire | pandas speedup where the user stays on pandas; no migration ask |
| `unrelated-data-format` | no-fire | CSV parsing as an ingestion/upload task, not analysis |

Current description baseline (omp, 3 runs each, before this expansion):

| Case | Trigger rate |
|---|---|
| explicit `pandas` request | 0/3 |
| implicit Python data question with a CSV | 2/3 |

Claude Code and Codex matched both cases 1/1 in the same check. Re-run after
any description edit; the negative-first wording was selected because it
removed the 2/3 false-load rate seen with the previous description. Treat
those numbers as anecdotal: they predate the expanded case set and the 10-run
requirement above.

Each result also reports input/output/total tokens. Cost is included when the
agent exposes it: Claude Code reports `total_cost_usd`, omp reports per-message
cost, and Codex reports tokens only. A full snapshot is:

```bash
python evals/trigger_eval.py --agent all --runs 1 --timeout 240 --jobs 4 --json
```

`--jobs` runs independent cases concurrently (default 4); use `--jobs 1` to
serialize. Use `--runs 3` or more before trusting a small difference between
variants.

## Self-check

```bash
python evals/run_eval.py --solution evals/reference_solution.py
```

This must pass on both tested versions; `tests/test_eval_harness.py` keeps it
that way.

## What this does not measure

- Trigger rate: whether the model loads the skill without being told.
- Code quality, performance, or token cost.
- Subjective answers.

For trigger evaluation, run the same prompt with and without the skill and
record whether the skill was read. For description optimization, use a
with-skill / without-skill comparison over a held-out prompt set.
