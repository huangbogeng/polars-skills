# Polars 2.0: Correctness Changes

Load this file when the project runs Polars 2.x, or when upgrading code
written for 1.x. The complete list is the official upgrade guide:
https://docs.pola.rs/releases/upgrade/2/

This page covers changes that alter results **without raising** and removals
that common data-analysis code hits first. Each example is executable on
Polars 2.0.0.

## Contents

Version check; execution order (streaming default); `concat` and `explode`;
casts and dtypes; expressions and selectors; SQL; removed names; migration
checklist.

## Check the version first

```python
import polars as pl

print(pl.__version__)  # "2.x" enables the behavior below; 1.x does not
```

Run the project's tests on both sides of an upgrade. A passing import check
does not catch silent changes in row order, dtypes, or row counts.

## Execution order is no longer incidental

On Polars 2.0, `LazyFrame.collect()` still defaults to `engine="auto"`, but
`"auto"` now resolves to the **streaming engine** for lazy queries. Eager
`DataFrame` operations keep the in-memory engine.

The streaming engine does not guarantee row order for operations that do not
require it: joins, `group_by`, `unpivot`, and others. Code that relied on
incidental order can now return the same rows in a different order.

If order is part of the answer, make it explicit:

```python
left = pl.LazyFrame({"k": [0, 1, 2], "label": ["a", "b", "c"]})
right = pl.LazyFrame({"k": [2, 1, 0], "value": ["x", "y", "z"]})

joined = left.join(
    right, on="k", how="left", maintain_order="left"
).collect()
assert joined["label"].to_list() == ["a", "b", "c"]
```

- `join(..., maintain_order=...)` accepts `"none"`, `"left"`, `"right"`,
  `"left_right"`, or `"right_left"`; verify the option on the installed
  version.
- `group_by(..., maintain_order=True)` preserves first-seen group order.
- A final `.sort(...)` is the portable choice when the consumer expects a
  specific order but the operation does not define one.
- `sink_*` was already routed through the streaming engine before 2.0, so
  this change does not alter sinks.
- `explain()` still prints the optimized IR plan unless
  `engine="streaming"` is passed explicitly.

To restore the 1.x default for a single query or a process:

```python
result = lf.collect(engine="in-memory")

pl.Config.set_engine_affinity("in-memory")  # process-wide; also via
# POLARS_ENGINE_AFFINITY=in-memory
```

## Reshaping: `concat` and `explode`

`pl.concat(..., how="horizontal")` now always requires equal heights.
Previously it padded the shorter frame with nulls; it now raises
`ShapeError`. `strict=False` no longer opts into padding. Use
`horizontal_extend` to pad explicitly, or pad before concatenating.

```python
a = pl.DataFrame({"a": [1, 2, 3]})
b = pl.DataFrame({"b": [4, 5]})

padded = pl.concat([a, b], how="horizontal_extend")
assert padded.height == 3
assert padded["b"].to_list() == [4, 5, None]
```

`explode()` now defaults to `empty_as_null=False`: an empty list produces
zero rows. A null list still produces one null row (`keep_nulls=True`).

```python
df = pl.DataFrame({"a": [[1, 2], [], [3]]})

assert df.explode("a")["a"].to_list() == [1, 2, 3]
assert df.explode("a", empty_as_null=True)["a"].to_list() == [
    1, 2, None, 3,
]
```

`unpivot` output order is not guaranteed under the streaming engine. Sort it
when order matters.

## Casts and dtypes

Casts that used to be lossy or implicit now raise. Replace them with the
named operation:

| Removed / changed | Use instead |
|---|---|
| `string.cast(pl.Date/pl.Datetime/pl.Time)` | `.str.to_date()` / `.str.to_datetime()` / `.str.to_time()` |
| integer ↔ `Categorical` / `Enum` cast | `.cat.to(dtype)` / `.cat.physical()` |
| scalar or flat column `cast(pl.List(...))` | `pl.list(expr)` |
| boolean `&`/`\|` integer | cast the boolean first: `pl.col("b").cast(pl.Int32)` |
| `Duration.std()` / `.var()` / `.ewm_*()` | cast to an integer unit first: `.dt.total_microseconds().std()` |

A signed integer combined with `UInt64` now produces **`Int128`**, not the
lossy `Float64` it produced before:

```python
lf = pl.LazyFrame(
    {"a": [1], "b": [1]},
    schema={"a": pl.Int64, "b": pl.UInt64},
)
assert lf.select((pl.col("a") + pl.col("b")).alias("r")).collect_schema()["r"] == pl.Int128
```

`is_in()` now requires a lossless type match instead of coercing lossily.
Pass a list or expression of the same type, or cast explicitly. A `Series`
needle of the same dtype is also deprecated in 2.0; use `.implode()` when a
list-shaped needle is intended.

```python
needle = pl.Series([1.99]).cast(pl.Int64).implode()
assert pl.Series([1]).is_in(needle).to_list() == [True]
```

## Expressions and selectors

Combining a selector with `pl.col(...)` through `&`, `|`, or `^` no longer
means a set operation on columns. It now falls through to the ordinary
element-wise operator, which can silently return computed columns instead of
a selection. Use a selector on both sides:

```python
df = pl.DataFrame({"a": [1, 2], "b": [3, 4], "mask": [1, 0]})

selected = df.select(pl.selectors.integer() & pl.selectors.by_name("mask"))
assert selected.columns == ["mask"]
```

Other silent changes:

- `pl.datetime(...)` and `pl.repeat(...)` name their output after the
  leftmost argument instead of `"datetime"` / `"repeat"`. Add `.alias()`
  when a name is part of the contract.
- `list/array.to_struct()` preserves outer nulls: a null list becomes a
  null struct, not a struct with null fields.
- `shift(None)` raises instead of returning nulls.
- SQL resolves after `collect()`; see below.

`cut()` and `qcut()` are deprecated in favour of `bin_intervals()`,
`bin_quantiles()`, and `bin_ranks()`. The `bin_*` methods are left-closed by
default, require `labels`, and keep equal values together.

```python
binned = pl.DataFrame({"x": [1, 2, 3, 4]}).select(
    pl.col("x").bin_quantiles(2, labels=False).alias("bucket")
)
assert binned["bucket"].null_count() == 0
```

## SQL

`pl.sql()`, `SQLContext.execute()`, and `LazyFrame.sql()` now defer parsing
and resolution to `collect()` or `collect_schema()`. An unknown table or
column no longer raises at construction time:

```python
ctx = pl.SQLContext(t=pl.LazyFrame({"a": [1]}))
query = ctx.execute("SELECT * FROM derived")  # no error yet
try:
    query.collect_schema()
except pl.exceptions.SQLInterfaceError:
    pass  # validate here instead of relying on the constructor
```

SQL numeric semantics also changed: exact numeric literals are `Decimal`,
decimal `*`/`/` use SQL scales, and `%` / `DIV` truncate toward zero. Cast
the SQL result when Python-side float behavior is required.

## Removed names that data code hits first

| Removed | Use instead |
|---|---|
| `melt()` | `unpivot()` (`index` / `on`) |
| `with_row_count()` | `with_row_index()` (default name `"index"`) |
| `fetch()` | `head(...).collect()` |
| `profile()` | none; inspect with `explain()` |
| `group_by(...).count()` | `group_by(...).len()` |
| `join_nulls=` | `nulls_equal=` |
| `join(how="outer")` | `how="full"` (with `coalesce=` as needed) |
| `scan_csv(dtypes=...)` | `schema_overrides=...` |
| `row_count_name=` / `row_count_offset=` | `row_index_name=` / `row_index_offset=` |
| `collect(streaming=True)` | `collect(engine="streaming")` |
| `top_k(..., descending=...)` | `reverse=...` |
| `rolling(..., by=...)` | `group_by=...` |

## Migration checklist

1. Pin the target version and run the existing test suite before editing.
2. Search for the removed names above and for `.cast(pl.Date)`,
   `.cast(pl.Datetime)`, `.cast(pl.Time)`, `.cast(pl.List`, `qcut`, `cut`.
3. Compare output **row order**, row counts, dtypes, and column names — not
   just values — on representative fixtures.
4. Make ordering explicit wherever the consumer depends on it.
5. Re-check null behavior around `explode`, casts, and `is_in`.
