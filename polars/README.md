# polars-skills

Official Polars agent skill for writing performant, idiomatic Python code
with the Polars DataFrame library. Built on the SKILL.md open standard and
compatible with Claude Code, OpenAI Codex, GitHub Copilot, and Cursor.

## What this skill does

When installed, this skill activates automatically for Polars tasks (writing,
debugging, or speeding up Polars code), pandas to Polars migrations, and
Python tabular data analysis on CSV, Parquet, or DataFrames when no library
has been chosen. It teaches agents to:

- Use the lazy API by default (`scan_csv`, `scan_parquet`, `.lazy()`,
  `.collect()`)
- Write native Polars expressions instead of `map_elements` or pandas-style
  operations
- Choose the correct context (`select`, `with_columns`, `filter`,
  `group_by`, `over`)
- Preserve calculation semantics while avoiding unnecessary materialization
- Avoid pandas API habits that produce wrong or slow results in Polars
- Handle Polars 2.0 behavior changes: streaming-default row order, removed
  casts, `explode` row counts, and renamed APIs

## Installation

**Claude Code marketplace:**

```
/plugin marketplace add polars-inc/skills
/plugin install polars@polars
```

**Codex marketplace:**

```bash
codex plugin marketplace add polars-inc/skills
codex plugin add polars@polars
```

**omp marketplace** (reads the Claude-compatible catalog):

```
/marketplace add polars-inc/skills
/marketplace install polars@polars
```

Start a session. The skill can load for Polars work or Python data processing
without a chosen library. Invoke it explicitly with `/polars:polars`
(Claude Code), `$polars` (Codex), or `/skill:polars` (omp).

**Install all three from a clone:**

```bash
python scripts/install_skill.py --agents all
python scripts/install_skill.py --agents all --scope project
```

Claude Code reads `~/.claude/skills/polars`; Codex and omp both read
`~/.agents/skills/polars` (project: `.claude/skills` and `.agents/skills`).

**Manual copy:**

Copy the `polars/` directory into the skills folder for your agent tool. No
build step required.

### Claude Code

```bash
git clone https://github.com/polars-inc/skills
cp -r skills/polars ~/.claude/skills/
cp -r skills/polars .claude/skills/
```

After a manual copy the skill command is `/polars` (no plugin namespace).

### OpenAI Codex

```bash
# User-level (Codex and omp both discover this)
cp -r polars ~/.agents/skills/

# Repo-level
cp -r polars .agents/skills/
```

Codex also reads the optional `agents/openai.yaml` sidecar for display name
and implicit-invocation policy; other agents ignore it. `.codex/skills` is
not the current discovery path.

### omp

```bash
# Shared with Codex
cp -r polars ~/.agents/skills/
cp -r polars .agents/skills/
```

omp also supports its native skill root (`~/.omp/agent/skills/`) and
`/skill:polars`; the shared `.agents/skills` path is enough for both Codex and
omp.

### Cursor

```bash
cp -r polars .cursor/skills/
```

### GitHub Copilot (VS Code)

Place the `polars/` directory in your VS Code agent skills folder.
See the GitHub Copilot Agent Skills documentation for the correct path.

## Compatibility

| Tool              | Supported | Discovery / entry |
|-------------------|-----------|-------------------|
| Claude Code       | Yes       | `~/.claude/skills`, `/polars:polars` |
| OpenAI Codex      | Yes       | `~/.agents/skills`, `$polars` |
| omp               | Yes       | `~/.agents/skills`, `/skill:polars` |
| GitHub Copilot    | Yes       | agent skills folder |
| Cursor            | Yes       | `.cursor/skills` |
| Any Agent Skills host | Yes   | `SKILL.md` + `references/` |

This skill covers single-node Polars only. It does not apply to Polars Cloud,
On-Prem deployments, distributed workloads, or GPU execution.

Examples are tested with Python 3.10 and Polars **1.44.2** and **2.0.0**.
Other versions require API verification; this is not a promise that every
Polars 1.x release supports the examples. `stable` documentation follows the
latest release, so check local signatures or matching-version docs.

## Repository structure

```
polars/
├── .claude-plugin/
│   └── plugin.json             # Claude Code plugin manifest
├── SKILL.md                    # Core skill: rules, patterns, context selection
├── agents/
│   └── openai.yaml             # optional Codex sidecar (display name, invocation)
├── scripts/
│   └── polars_api.py           # live API search/doc against the installed Polars
└── references/
    ├── contexts.md             # select, with_columns, filter, group_by, over
    ├── expressions.md          # str, dt, list, struct, selectors, cast, when/then
    ├── insight-recipes.md      # natural-language question to query recipes
    ├── joins.md                # equi/asof/where/cross joins, validate, fan-out
    ├── lazy-api.md             # scan options, query plan inspection, streaming
    ├── pandas-to-polars.md     # API pattern differences and correctness traps
    ├── reshaping.md            # pivot, unpivot, transpose, concat, explode, gather
    └── version-2.md            # Polars 2.0 streaming, cast, dtype, and API changes
```

Reference files are loaded on demand. The agent reads them only when the
task requires detail beyond what is in `SKILL.md`.

## Live API lookup

`scripts/polars_api.py` introspects the installed Polars package, so it
returns the exact signatures and docstrings for the project's version without
network access or an MCP server:

```bash
python scripts/polars_api.py version
python scripts/polars_api.py search group_by
python scripts/polars_api.py doc LazyFrame.join
python scripts/polars_api.py browse Expr.str
python scripts/polars_api.py doc Expr.str.contains --output json
```

Run it with the project's interpreter (or `uv run`) so the version matches
the code being written.

## Companion: polars-mcp

This skill works alongside `polars-mcp`, a local MCP server that
introspects your installed Polars package and exposes its API for live
method lookup. The skill provides methodology; the MCP provides
version-accurate API verification.

Source: https://github.com/r-brink/polars-mcp

Install using `uvx` and specify the Polars version you use in your project:

```bash
# Latest Polars
uvx --with polars polars-mcp

# Pin to your project's Polars version
uvx --with polars==1.44.2 polars-mcp
```

Configure your agent tool:

```json
{
  "mcpServers": {
    "polars-mcp": {
      "command": "uvx",
      "args": ["--with", "polars==1.44.2", "polars-mcp"]
    }
  }
}
```

The MCP exposes three tools the skill uses for API verification:

- `polars_search_api` -- find methods and functions by keyword
- `polars_browse` -- explore all methods in a class or namespace
- `polars_get_docstring` -- get the full signature and docs for a specific API element

The skill works without the MCP. Use local `help()` / `inspect.signature()`
or matching-version docs.pola.rs pages when it is unavailable. Installing
the MCP is optional; its Polars version must match the project for accurate lookups.

## License

MIT
