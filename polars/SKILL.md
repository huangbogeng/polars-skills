---
name: polars
description: >
  Write fast, idiomatic lazy Polars in Python and preserve exact results.
  Use for tasks that clean, join, aggregate, reshape, or analyze tabular data
  with Polars (DataFrame, LazyFrame, scan_csv, scan_parquet, expressions);
  migrations from pandas to Polars; debugging or speeding up existing Polars
  code; and Python tabular analysis on CSV, Parquet, or DataFrames when no
  library has been chosen, even if the user never says "Polars". Covers
  single-node CPU Polars, including streaming. Scope is single-node: Polars
  Cloud, On-Prem, distributed, and GPU workloads are out of scope.
license: MIT
metadata:
  author: Polars
  homepage: https://pola.rs
  tags: polars, dataframes, lazyframe, data-processing, data-analysis, python
compatibility: Python 3.10+. Examples are tested with Polars 1.44.2 and
  2.0.0; verify API availability and behavior on other versions.
---

# Polars

## Default stance

Use Polars when the task or project calls for it, or when no Python data
processing library has been chosen. Prefer the lazy API for query pipelines:
build the plan before executing so Polars can optimize across operations.
Preserve the requested results, precision, ordering, and library contracts.

When a query starts from an in-memory `DataFrame` — a function argument,
`pl.from_pandas(pdf)`,
`pl.DataFrame(...)`, or an earlier `collect()` — call `.lazy()` before the
first operation and collect at the result boundary. `.lazy()` wraps the data
without copying it. Keep necessary materialization boundaries for eager-only
APIs, runtime decisions, or unsupported operations; a simple eager operation
or an explicitly eager interface does not need a wrapper for its own sake.

```python
# start from a file
pl.scan_csv("data.csv").filter(...).collect()

# start from an existing DataFrame — wrap it first
df.lazy().filter(...).group_by(...).agg(...).collect()

# avoid for a file-backed pipeline: materializing before filtering
df = pl.read_csv("data.csv")
df.filter(...).group_by(...).agg(...)
```

## From question to insight

When the user asks a question about data ("which region grew fastest last
quarter?"), inspect the schema and relevant values, define the calculation,
then build the query. Refine it if validation reveals an incorrect assumption.

1. **Discover the schema first. Never guess column names or dtypes.**

```python
lf = pl.scan_csv("sales.csv")        # or scan_parquet / scan_ndjson
print(lf.collect_schema())           # resolves schema; may inspect source data/metadata
print(lf.head(5).collect())          # eyeball values, formats, dirt
```

2. **Translate the question into a lazy query.** Define periods, missing
   values, tie-breaking, and denominators as needed; state assumptions that
   affect the answer ("growth = revenue vs. previous calendar quarter").
3. **Use scan options for known source conventions**, such as
   `null_values=["N/A", ""]` or `try_parse_dates=True`. Treat identifiers and
   null sentinels according to the data contract; validate failed casts.
4. **Collect at the result boundary**, then sanity-check before answering — the
   checks are at the end of `references/insight-recipes.md`.
5. **Answer with numbers, not just code.** Lead with the insight, show the
   supporting table, and keep the LazyFrame around so follow-ups extend the
   chain instead of rebuilding it.

## Core rules

- **Prefer native expressions.** Use expressions when they implement the
  required semantics. Python UDFs can limit optimization and add overhead,
  especially per-element calls. Use a tested UDF or batch operation when
  necessary, with explicit output types and appropriate performance checks.
- **Filter early when semantics allow.** Moving a filter across a window,
  aggregate, cumulative operation, or outer join can change results. Keep
  the logical order and let the optimizer push safe predicates down.
- **Group independent expressions.** Expressions in one `with_columns()`
  cannot reference sibling aliases created in that call; use another context
  for dependent calculations. Polars can combine independent contexts.
  When the *same* operation applies to several columns, prefer expansion:
  `pl.col("a", "b")`,
  `pl.col(pl.Float64)`, `pl.col("^sales_.*$")`, `cs.numeric()`,
  `pl.all().exclude("id")`. Rename the expanded set with `.name.suffix("_x")`
  / `.name.prefix()` / `.name.map(fn)`; `.alias()` names one output only.
  Selector catalogue and caveats: `references/expressions.md`.
- **Avoid unnecessary materialization.** Collect at an intentional boundary
  or use a sink for large outputs. Repeated collects can repeat source work.
- **Control types and precision.** Columns have one dtype, though operations
  can coerce compatible types. Cast when required by the data contract.
  `strict=False` turns invalid casts into nulls; inspect those losses.
  Float downcasts and rounding can change results and need an explicit
  precision policy.
- **Investigate warnings.** Resolve performance and correctness warnings
  where practical. If a warning is intentionally accepted, document why and
  scope any suppression to that specific warning and operation.

```python
import polars.selectors as cs

# avoid: same operation, one expression per column
lf.with_columns([(pl.col(c) * 1.1).alias(f"{c}_adj") for c in ["a", "b", "c"]])

# prefer: one expression, expanded by the schema
lf.with_columns((pl.col("a", "b", "c") * 1.1).name.suffix("_adj"))
lf.with_columns(cs.numeric().fill_null(0))          # in place, same names
```

## Canonical query pattern

This example filters orders before joining and aggregating. Choose the
operation order according to the calculation; joins can increase row counts.

```python
customers = pl.scan_csv("customers.csv")

result = (
    pl.scan_csv("orders.csv")                    # 1. lazy source
    .filter(pl.col("year") == 2024)              # 2. filter early
    .join(customers, on="customer_id", how="left")   # 3. join filtered data
    .with_columns(                               # 4. add computed columns
        (pl.col("revenue") - pl.col("cost")).alias("profit")
    )
    .group_by("region")                          # 5. group
    .agg(                                        # 6. aggregate
        pl.col("profit").sum().alias("total_profit"),
        pl.col("profit").mean().alias("avg_profit"),
        pl.len().alias("count"),
    )
    .filter(pl.col("count") > 10)                # 7. filter groups
    .sort("total_profit", descending=True)       # 8. sort
    .select("region", "total_profit", "avg_profit")  # 9. final columns
    .collect()                                   # 10. execute once
)
```

## Context selection

| Context | Use when | Output |
|---|---|---|
| `select()` | Choosing or transforming columns | Only specified columns |
| `with_columns()` | Adding or replacing columns | All columns plus new |
| `filter()` | Removing rows | Same columns, fewer rows |
| `group_by() + agg()` | Aggregating per group | One row per group |
| `over()` | Group aggregate broadcast to all rows | Same shape as input |
| `sort()` | Ordering rows | Same shape, reordered |
| `join()` | Combining two frames | Columns from both |

The critical distinction: `group_by().agg()` returns one row per group;
`over()` keeps all rows and broadcasts the group result back — use it inside
`with_columns()` when every row needs its group's aggregate.

## Gotchas

Each of these fails silently or with a confusing error.

- **Strings in `then()`/`otherwise()` are column names, not values.**
  `pl.when(c).then("adult")` reads a column called `adult` (or raises
  `ColumnNotFoundError`). Wrap literals: `.then(pl.lit("adult"))`.
- **Conditional branches must each be valid.** `when/then/otherwise` does
  not short-circuit evaluation; a condition cannot protect an invalid cast,
  out-of-bounds access, or a UDF that fails on the other rows.
- **Null comparisons drop rows silently.** `filter(pl.col("v") > 2)`
  excludes nulls because `null > 2` is null, which is falsy. If nulls
  should be kept: `(pl.col("v") > 2) | pl.col("v").is_null()`.
- **Use `&`, `|`, `~` with parentheses around each condition.** Python's
  `and`/`or`/`not` raise on expressions, and without parentheses operator
  precedence binds the comparison wrong:
  `(pl.col("a") > 1) & (pl.col("b") < 5)`.
- **A bare aggregation in `with_columns()` broadcasts the global value to
  every row.** `with_columns(pl.col("v").mean())` fills the column with the
  overall mean — it does not error. Add `.over("group")` for the per-group
  value aligned to each row.
- **Duplicate output names raise `DuplicateError`.** A computed column
  keeps its source name; `select(pl.col("p"), pl.col("p") * 1.1)` fails.
  Use `.alias()` for a distinct derived output, or `.name.suffix(...)` for
  an expansion. Keep the source name when intentionally replacing a column.
- **Regex column selection needs `^...$` anchors.** `pl.col("sales_.*")` is
  read as a literal column name and raises `ColumnNotFoundError`;
  `pl.col("^sales_.*$")` expands. One `col()` call cannot mix names with
  dtypes — use a selector instead.
- **Nulls don't match in joins by default.** Rows with null keys silently
  drop out of inner joins; pass `nulls_equal=True` if they should match.
- **On Polars 2.0, lazy row order is not guaranteed.** `collect()` defaults
  to the streaming engine, which may reorder joins, `group_by`, and
  `unpivot`. Sort explicitly or use `maintain_order` when the consumer
  depends on order; see `references/version-2.md`.
- **pandas names don't transfer.** No index, no `iloc`, no `groupby`; verify
  any method you're not certain about (see below) rather than assuming the
  pandas spelling exists.

## Version and API verification

Examples target Polars 1.44.2 and 2.0.0, not every 1.x release. Check
`python -c "import polars as pl; print(pl.__version__)"` in the project's
interpreter. Code written for 1.x must be re-checked on 2.0: the streaming
engine is now the lazy default, several casts were removed, and some
operations changed results silently. Read `references/version-2.md` when
upgrading or when the project pins 2.x.

Verify uncertain APIs against the installed version:

- **Bundled lookup (no MCP or network):** from the skill directory run
  `python scripts/polars_api.py doc LazyFrame.join`,
  `python scripts/polars_api.py search join`, or
  `python scripts/polars_api.py browse Expr.str`. The script introspects the
  Polars package importable by that interpreter, so run it with the project's
  environment (`uv run`, the project venv, or its Python).
- **Existing MCP:** if a matching-version `polars-mcp` is configured,
  `polars_search_api("filter")` finds methods by keyword,
  `polars_browse("Expr.str")` explores a namespace, and
  `polars_get_docstring("Expr.str.contains")` gets the exact signature.
- **Local introspection:** use `help()` or `inspect.signature()` on the
  installed package. A small executable example can confirm semantics.
- **Official docs:** the top of `references/expressions.md` maps categories
  to https://docs.pola.rs. Match the documentation version to the project;
  `stable` follows the latest release. A lookup does not require installing
  an MCP server or changing project dependencies.

## When to load references

Read the relevant sections when their detail is needed. Each file opens with
a `## Contents` line; unrelated references need not be loaded.

- **Upgrading from Polars 1.x or running 2.x** — read
  `references/version-2.md` for streaming-order, cast, dtype, SQL, and
  removed-API changes that alter results without raising.
- **Translating pandas → Polars** — read
  `references/pandas-to-polars.md` for
  API-difference traps absent from this file.
- **Answering a natural-language data question** — read
  `references/insight-recipes.md` for ready-made query shapes (top-k,
  period-over-period, distributions, resampling, trailing/sliding windows).
- `references/contexts.md` — detailed behavior of `select`, `with_columns`,
  `filter`, `group_by`/`agg`, `over` (window mapping strategies), `sort`,
  and `join` (basics; see `joins.md` for the rest).
- `references/joins.md` — `join_asof` (nearest-in-time), `join_where`
  (inequality / range), `validate=` cardinality, cross joins, `suffix` /
  `coalesce` / `nulls_equal`, and diagnosing join fan-out.
- `references/reshaping.md` — `pivot` / `unpivot` / `transpose`, `pl.concat`
  (vertical / horizontal / diagonal / align), `explode` ↔ `implode`,
  `gather`.
- `references/expressions.md` — string, temporal, list, struct, expansion
  and selector syntax; casting; null handling; conditionals; plus the
  fetch-map described above.
- `references/lazy-api.md` — scan options for dirty data, query plan
  inspection with `explain()`, streaming engine for larger-than-memory
  data, `sink_parquet`, and compute performance levers (dtypes,
  categoricals, memory).
