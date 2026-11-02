# Performance

Load this file when tuning a Polars query, choosing dtypes or IO, or
reviewing code that is slower than expected. Examples target Polars 2.1;
version-specific notes are marked.

## Contents

Measure first; keep work in the engine; column operations without copies;
move less data; plan once; memory and types; IO; concurrency; anti-patterns;
references.

## Measure first

Performance work starts with a plan and a benchmark, not with a list of
flags. Inspect the optimized plan, estimate memory, then time the actual
query on representative data.

```python
lf = (
    pl.scan_parquet("events/*.parquet")
    .filter(pl.col("event") == "purchase")
    .select("user_id", "ts", "amount")
)
print(lf.explain())          # optimized IR plan
print(lf.explain(optimized=False))
```

- `explain()` shows predicate/projection pushdown and join order.
- `show_graph(plan_stage="physical")` renders the streaming physical plan;
  it needs Graphviz.
- `pl.explain_all([lf1, lf2])` shows shared subplans for diverging queries.
- `df.estimated_size()` estimates in-memory bytes; `df.n_chunks()` reports
  fragmentation.
- `LazyFrame.profile()` was removed in 2.0. Use `explain()` and wall-clock
  benchmarks instead.
- Polars is not universally faster than pandas or NumPy. Measure the actual
  operation on representative data; different engines and data shapes win in
  different cases.

## Keep work in the engine

Use `scan_*` and native expressions so the Rust engine can parallelize and
optimize. Do not move per-row work into Python.

```python
lf = pl.scan_parquet("events/*.parquet")
result = lf.filter(pl.col("amount") > 0).with_columns(
    day=pl.col("ts").dt.truncate("1d"),
    share=pl.col("amount") / pl.col("amount").sum().over("user_id"),
)
```

- Prefer an expression over `map_elements` / `map_batches`. A per-value
  Python call serializes every value and disables native execution.
- Use `over()` instead of `group_by().agg()` plus a join back when every row
  needs its group aggregate.
- Group independent expressions in one `with_columns()`; they run in
  parallel, and window expressions over the same group can be cached.
- When no native expression exists, use `map_batches` with a compiled
  function (NumPy ufunc or Numba `@guvectorize`), not `map_elements`.

```python
import numpy as np

df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})
native = df.select(np.log(pl.col("x")).alias("log"))
assert native.height == 3

batched = df.select(
    pl.col("x").map_batches(np.log, return_dtype=pl.Float64).alias("log")
)
assert batched.height == 3
```

NumPy ufuncs can be passed directly as expressions; do not call
`to_numpy()` first unless the operation genuinely needs an ndarray. A
generalized ufunc receives no null mask, so Polars raises if the Series
contains missing data; fill or drop nulls first.

## Column operations without copies

Polars DataFrames are immutable from the API's perspective. Add or replace
columns with `with_columns()`; do not copy a DataFrame and mutate it
column by column.

```python
df = pl.DataFrame({"a": [1, 2]})
result = df.with_columns(b=pl.col("a") + 1)
assert df.columns == ["a"]
assert result.columns == ["a", "b"]

try:
    df["c"] = [3, 4]
except TypeError:
    pass
else:
    raise AssertionError("Polars rejects item assignment; use with_columns")
```

`with_columns` returns a new DataFrame, but **does not copy the existing
data**; untouched columns are shared. `DataFrame.clone()` is also cheap and
does not copy data, but it is rarely needed: prefer passing immutable frames
through the query.

- One call with independent expressions is clearer and lets Polars run them
  together. Dependent columns need another context because sibling aliases
  are not visible in the same `with_columns`.
- Use expression expansion instead of a Python loop: `pl.col("a", "b")` or
  `(cs.numeric() * 2).name.suffix("_x")`.
- Do not build `df = df.with_columns(...)` in a per-column loop; use one
  expansion, or a separate call only when dependencies require it.

## Move less data

- Start with `scan_*`, not `read_*`, for file-backed pipelines.
- Select only the columns the result needs; projection pushdown happens at
  the scan.
- Filter before `group_by`, joins, and wide `with_columns` when those rows
  should not participate. Do not move a filter across a window, aggregate,
  cumulative operation, or outer join: that changes semantics.
- Use `head()` while developing on large inputs; validate on full data
  before answering.
- Slice pushdown means `head`/`slice` can limit what the scan reads.

## Plan once

Each intermediate `collect()` materializes data and discards the plan.
Collect once at the result boundary. When one base query feeds several
outputs, use `collect_all` so Polars can share the subplan.

```python
base = pl.LazyFrame({"a": [1, 2, 3]}).filter(pl.col("a") > 1)
total = base.select(pl.col("a").sum().alias("s"))
mean = base.select(pl.col("a").mean().alias("m"))

frames = pl.collect_all([total, mean])
assert frames[0]["s"].to_list() == [5]
assert frames[1]["m"].to_list() == [2.5]
assert "CACHE" in pl.explain_all([total, mean])
```

Use lazy sinks to write several outputs from one query:

```python
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    lf = pl.LazyFrame({"k": ["a", "b"], "v": [1, 2]})
    parquet = lf.sink_parquet(Path(tmp) / "out.parquet", lazy=True)
    csv = lf.sink_csv(Path(tmp) / "out.csv", lazy=True)
    pl.collect_all([parquet, csv])
    assert (Path(tmp) / "out.parquet").exists()
    assert (Path(tmp) / "out.csv").exists()
```

For partitioned output, pass `pl.PartitionBy` to a sink:

```python
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    lf = pl.LazyFrame({"k": ["a", "b", "a"], "v": [1, 2, 3]})
    lf.sink_parquet(
        pl.PartitionBy(tmp, key="k", max_rows_per_file=1)
    )
    assert list(Path(tmp).rglob("*.parquet"))
```

`LazyFrame.cache()` forces a cache node, but the docs explicitly do not
recommend it: the optimizer usually does a better job. Reach for it only
after measuring a repeated expensive subplan.

## Memory and types

- **Projection** is the cheapest memory win: read only needed columns.
- **Integer downcasts** reduce memory and bandwidth, but only after proving
  the value range. `Int8`/`UInt8` for 0..255, `Int16`/`UInt16` for the next
  range, and so on; use a strict cast and validate boundaries.
- **Prefer `Enum` over `Categorical`** when categories are known up front:
  low-cardinality strings encode as integers; the mapping is fixed, ordered,
  and avoids global-mapping locks. Unknown values raise on a strict cast.
  Use `Categorical` only when categories grow dynamically; it changes dtype
  and can affect sorting.
- **`Categories`** can choose a smaller physical type (`UInt8`/`UInt16`/
  `UInt32`) when the category count is low. `StringCache` is deprecated and
  does nothing in current Polars.
- **Prefer `Array` over `List`** for fixed-shape values; it is more memory
  efficient and faster. Use `List` only when lengths vary or you need
  list-only operations.
- **Decimal vs integer minor units**: `Decimal` avoids binary float error but
  is heavier than integer cents. `str.to_decimal(scale=2)` is keyword-only;
  reducing scale loses digits. Choose from the precision contract and
  measure; do not add `.round(2)` just to make money look tidy.
- **Object dtype** holds arbitrary Python objects and limits native
  expressions. Keep native dtypes and isolate object-based fallbacks.
- **Float32** is lossy; retain `Float64` unless a measured tolerance allows
  otherwise.
- **`rechunk()`** copies; do it only after many small chunks (for example
  after concatenating many frames) when row-wise work follows.

```python
colors = pl.Series(
    ["red", "blue", "red"], dtype=pl.Enum(["red", "blue"])
)
vectors = pl.Series(
    [[1, 2], [3, 4]], dtype=pl.Array(pl.Int64, 2)
)
assert colors.dtype == pl.Enum(["red", "blue"])
assert vectors.dtype == pl.Array(pl.Int64, 2)
```

## IO

- Store repeated scans as Parquet; it is columnar and carries statistics.
- `sink_parquet` / `write_parquet` accept `compression`, `compression_level`,
  `row_group_size`, and `statistics`. Defaults are reasonable; tune only
  with a benchmark.
- Partition on columns used by later filters (`partition_by=` or
  `pl.PartitionBy`) so scans can skip files.
- Cloud scans still get predicate/projection pushdown; keep filters and
  selections before `collect()`.
- Delta and Iceberg scans (`scan_delta`, `scan_iceberg`) are lazy and can
  use metadata statistics; see `lazy-api.md` and `iceberg.md`.

```python
lf.sink_parquet(
    "out.parquet",
    compression="zstd",
    row_group_size=250_000,
    statistics=True,
)
```

## Concurrency

- Polars already distributes work across CPU cores. Do not add Python
  `multiprocessing` by default; it often competes with Polars rather than
  helping. Use it only when a single-threaded external library is the
  bottleneck.
- If multiprocessing is required, use the `"spawn"` start method, not
  `"fork"`; `fork` copies locks from the multithreaded parent and can
  deadlock.
- `POLARS_MAX_THREADS` can override the thread pool size, but must be set
  before importing Polars. The docs recommend leaving it to the engine
  except in special contexts such as a PySpark UDF.
- `pl.thread_pool_size()` reports the current pool size.
- `POLARS_OOC_*` and `POLARS_ENGINE_AFFINITY` are covered in
  `lazy-api.md`.

## Anti-patterns

- Repeated `.collect()`; use one plan and `collect_all` for branches.
- `map_elements` for logic that an expression can express.
- `df["new"] = ...` or clone-to-mutate; use `with_columns`.
- Per-column `with_columns` loops; use expansion.
- Removed APIs: `collect(streaming=True)`, `pivot(columns=...)`,
  `profile()`, `StringCache`.
- Dask/Vaex for larger-than-RAM; use streaming and out-of-core spilling.
- Sorting before equi joins; downcasting without a range check;
  `Categorical` when `Enum` works; `List` when `Array` works; `to_numpy()`
  round trips without a measured reason.

## References

- Lazy API: https://docs.pola.rs/user-guide/concepts/lazy-api/
- Optimizations: https://docs.pola.rs/user-guide/lazy/optimizations/
- Query plan: https://docs.pola.rs/user-guide/lazy/query-plan/
- Execution: https://docs.pola.rs/user-guide/lazy/execution/
- Multiplexing: https://docs.pola.rs/user-guide/lazy/multiplexing/
- Sources and sinks: https://docs.pola.rs/user-guide/lazy/sources_sinks/
- Streaming: https://docs.pola.rs/user-guide/concepts/streaming/
- UDFs: https://docs.pola.rs/user-guide/expressions/user-defined-python-functions/
- NumPy functions: https://docs.pola.rs/user-guide/expressions/numpy-functions/
- Categoricals and enums: https://docs.pola.rs/user-guide/expressions/categorical-data-and-enums/
- Lists and arrays: https://docs.pola.rs/user-guide/expressions/lists-and-arrays/
- Multiprocessing: https://docs.pola.rs/user-guide/misc/multiprocessing/
- Thread pool: https://docs.pola.rs/api/python/stable/reference/api/polars.thread_pool_size.html
- Modern Polars performance chapter: https://kevinheavey.github.io/modern-polars/performance.html
