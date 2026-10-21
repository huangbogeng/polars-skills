# Polars Plugin Marketplace

Official Claude Code plugin marketplace for [Polars](https://pola.rs) — the fast DataFrame library for Python.

## Install

| Agent | Install |
|---|---|
| Claude Code | `/plugin marketplace add polars-inc/skills`<br>`/plugin install polars@polars` |
| Codex | `codex plugin marketplace add polars-inc/skills`<br>`codex plugin add polars@polars` |
| omp | `/marketplace add polars-inc/skills`<br>`/marketplace install polars@polars` |

Start a session. Claude Code selects the skill for Polars work, pandas to
Polars migrations, and Python tabular analysis with no chosen library. To
invoke it explicitly, type `/polars:polars`
(Codex: `$polars`, omp: `/skill:polars`).

### One install for all three

Claude Code reads `~/.claude/skills`; Codex and omp both read
`~/.agents/skills`. Copy or link the same `polars/` directory into both:

```bash
python scripts/install_skill.py --agents all          # user-level, symlink
python scripts/install_skill.py --agents all --scope project
python scripts/install_skill.py --agents codex,omp --copy
```

The installer refuses to overwrite an existing install unless `--force` is
passed; `--dry-run` shows the destinations. Project scope writes to
`.claude/skills/polars` and `.agents/skills/polars` in the current directory.

## Plugins

| Plugin | Description |
|--------|-------------|
| `polars` | Write idiomatic Polars, analyze data with lazy queries, and migrate pandas pipelines while preserving semantics |

## Manual install (no marketplace)

Clone the repo and use the installer, or copy `polars/` yourself:

```bash
git clone https://github.com/polars-inc/skills
cd skills

# Claude Code -> ~/.claude/skills
# Codex + omp -> ~/.agents/skills
python scripts/install_skill.py --agents all
python scripts/install_skill.py --agents all --scope project
```

## Repository layout

```text
polars/                  canonical skill: SKILL.md, references/, scripts/, agents/
plugins/polars/          Codex plugin wrapper (.codex-plugin + vendored skill copy)
.agents/plugins/         Codex marketplace catalog
.claude-plugin/          Claude Code / omp marketplace catalog
evals/                   deterministic with-skill / without-skill task harness
scripts/                 cross-agent installer + Codex copy sync
tests/                   example, API lookup, eval, and packaging tests
```

The Codex copy under `plugins/polars/skills/polars` is generated. After
editing `polars/`, run `python scripts/sync_codex_plugin.py`; CI fails when
the copy is stale.

## Live API lookup

`polars/scripts/polars_api.py` introspects the installed Polars package, so
lookups match the project's version without an MCP server or network:

```bash
python polars/scripts/polars_api.py doc LazyFrame.join
python polars/scripts/polars_api.py search group_by
python polars/scripts/polars_api.py browse Expr.str
```

## Companion: polars-mcp

`polars-mcp` remains an optional alternative for live API lookup:

```bash
uvx --with polars polars-mcp
```

See [r-brink/polars-mcp](https://github.com/r-brink/polars-mcp) for setup.

## Validation

Run the format validator with Python 3.11+ (the skill examples require 3.10+):

```bash
uvx --from skills-ref==0.1.1 agentskills validate polars
```

Run the examples on both tested Polars versions from the repository root:

```bash
uv run --no-project --python 3.10 --with polars==1.44.2 python -W error::DeprecationWarning -m unittest discover -s tests -v
uv run --no-project --python 3.10 --with polars==2.0.0 python -W error::DeprecationWarning -m unittest discover -s tests -v
```

Tests execute selected documentation code blocks and check their results on
fixtures covering ties, calendar gaps, zero denominators, nulls, JSON schemas,
joins, pivots, and Polars 2.0 breaking changes. Every fenced Python block is
also parsed, so examples cannot drift into invalid syntax. Polars 2.0-only
examples (for example `bin_quantiles` and streaming-order behavior) are
skipped on 1.x and run on 2.0. The suite also exercises the bundled live API
lookup. CI runs these checks on pushes and pull requests and before the
release job.

## Evaluation

`evals/` contains a deterministic task-success harness. It makes no model
calls: an agent writes a solution module, and the runner grades it against
exact expected DataFrames with hidden assertions.

```bash
python evals/run_eval.py --list
python evals/run_eval.py --prompt validate_join
python evals/run_eval.py --solution path/to/solution.py --json
python evals/run_eval.py --solution evals/reference_solution.py
```

Run each task with and without the skill to measure the skill's lift. The
tasks cover deterministic top-N ties, calendar gaps and zero denominators,
join cardinality, empty vs null lists, and expression expansion.

Trigger behavior is measured separately across all three agents:

```bash
python evals/trigger_eval.py --dry-run
python evals/trigger_eval.py --agent all --runs 3
```

It installs a uniquely named probe copy in each agent's discovery path and
checks tool events for an actual skill read. This spends model calls and is
not part of CI. See `evals/README.md` for thresholds and the protocol. These
checks do not measure token cost or subjective quality.

## License

MIT
