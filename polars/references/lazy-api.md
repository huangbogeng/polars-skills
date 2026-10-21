# Lazy API: Scans, Plans, Streaming

The lazy API builds a query plan that Polars optimizes before execution:
predicate pushdown (filters applied during the scan), projection pushdown
(only needed columns read), slice pushdown, common subexpression elimination,
and automatic parallelism — which is why queries start with `scan_*` and end
with a single `collect()`.

## Contents

Starting a lazy query; starting from an in-memory DataFrame; scan options
that prevent downstream pain; schema discovery before execution;
inspecting the plan; executing; larger-than-memory streaming and sinks
(including the 2.0 streaming default and its ordering implications);
compute performance levers (dtypes, categoricals, memory).

## Starting a lazy query

```python
lf = pl.scan_csv("data.csv")
lf = pl.scan_parquet("data.parquet")      # also: scan_ndjson, scan_ipc
lf = pl.scan_parquet("events/*.parquet")  # globs scan many files as one
lf = df.lazy()                            # from an eager DataFrame
```

## Starting from an in-memory DataFrame

Prefer `.lazy()` for a multi-step query on a `DataFrame` so Polars can
optimize across operations. A function with a `DataFrame -> DataFrame`
signature can keep that signature — go lazy on entry, collect on exit:

```python
def top_regions(df: pl.DataFrame, n: int) -> pl.DataFrame:
    return (
        df.lazy()                                  # wrap
        .filter(pl.col("revenue") > 0)
        .group_by("region")
        .agg(pl.col("revenue").sum().alias("total"))
        .sort("total", descending=True)
        .head(n)
        .collect()                                 # unwrap
    )
```

If a required step exists only on `DataFrame`, collect there. Re-enter with
`.lazy()` if the remaining work benefits from a query plan; preserve the
interface and any materialization needed for runtime validation.

## Scan options that prevent downstream pain

Handle dirty data at the scan so the rest of the query stays strict:

```python
lf = pl.scan_csv(
    "data.csv",
    null_values=["N/A", "NULL", ""],   # become real nulls, not strings
    try_parse_dates=True,              # ISO-like strings -> temporal dtypes
    infer_schema_length=10_000,        # sample more rows for dtype inference
    schema_overrides={"zipcode": pl.String},  # pin dtypes inference gets wrong
)
```

If a numeric column still arrives as String, something non-numeric is in it:
inspect with `lf.head(20).collect()`, then extend `null_values` or use
`.cast(pl.Float64, strict=False)` deliberately.

## Schema discovery before execution

```python
lf.collect_schema()          # {name: dtype}; may read source data/metadata
lf.collect_schema().names()
lf.head(5).collect()         # tiny peek at actual values
```

Use `collect_schema()` when names or dtypes are unknown or to validate a
plan. It can read file metadata, sample CSV records, or make network requests;
it is not a guarantee of zero I/O. Reuse an already-known schema where useful.

## Inspecting the plan

```python
lf = pl.scan_csv("large.csv").select("a", "b").filter(pl.col("a") > 100)

print(lf.explain())                    # optimized plan as text
print(lf.explain(optimized=False))     # naive plan, for comparison
```

In the optimized plan, look for the filter inside the scan node (predicate
pushdown) and `PROJECT 2/47 COLUMNS` (projection pushdown). A predicate that
did not push down usually depends on a computed column — filter on source
columns where possible.

## Executing

```python
result = lf.collect()                  # optimize + run, returns DataFrame
```

Collect at intentional result boundaries. For debugging a long chain, prefer
`lf.head(20).collect()` over collecting the full intermediate.

LazyFrames are cheap immutable values, so when several queries share a scan,
build them from one base and collect them together — common subplans are then
computed once. This is also the right shape for conversational analysis: keep
the base, extend it per follow-up question, collect once per answer.

```python
base = pl.scan_parquet("orders/*.parquet").filter(pl.col("year") == 2024)
by_region = base.group_by("region").agg(pl.col("rev").sum())
by_month = base.group_by(pl.col("date").dt.month()).agg(pl.col("rev").sum())
region_df, month_df = pl.collect_all([by_region, by_month])
```

## Larger-than-memory: streaming and sinks

```python
result = lf.collect(engine="streaming")    # explicit; process in batches
```

The streaming engine can reduce peak memory by executing supported operations
in chunks. Some operations still need substantial memory, and `collect()`
materializes the final output.

On **Polars 2.0**, `engine="auto"` (the default) resolves to the streaming
engine for lazy queries, so a plain `lf.collect()` is already streaming.
Eager `DataFrame` operations still use the in-memory engine. The streaming
engine does not guarantee row order for operations that do not require it —
joins, `group_by`, `unpivot`, and others. If order is part of the result,
sort after collecting or use the operation's `maintain_order` option:

```python
left.join(right, on="id", how="left", maintain_order="left").collect()
lf.group_by("region", maintain_order=True).agg(pl.len()).collect()
```

To restore the 1.x default, pass `engine="in-memory"` per query, set
`pl.Config.set_engine_affinity("in-memory")`, or set
`POLARS_ENGINE_AFFINITY=in-memory`. `explain()` still renders the optimized
IR plan unless `engine="streaming"` is passed explicitly. On 1.x, passing
`engine="streaming"` is opt-in and `maintain_order` on joins depends on the
installed version; verify it. See `version-2.md` for the full set of 2.0
changes.

For large outputs, sink straight to disk:

```python
lf.sink_parquet("out.parquet")
lf.sink_csv("out.csv")
lf.sink_ndjson("out.ndjson")
```

Use streaming or sinks when the input is much larger than memory, or when a
regular `collect()` is killed by the OS.

## Compute performance levers

Measure these options on representative data and verify their effect on
results and schema:

- **Categorical / Enum for low-cardinality strings.** A column with few
  distinct values (status, country, segment) stores each value once as an
  integer code: `cast(pl.Categorical)` when the set comes from the data,
  `cast(pl.Enum([...]))` when it is fixed and known. This can reduce memory
  and speed up grouping or joining; it changes dtype and can affect sorting.
  Enum values outside the declared set raise on a strict cast.
- **Integer downcasts require range checks.** Use strict casts and verify
  that the narrower dtype covers both current values and the intended input
  range. Pin source types with `schema_overrides=` only when appropriate.
- **Float downcasts are lossy.** `Float64 → Float32` reduces storage but
  changes precision: `16777217.0` becomes `16777216.0`. Retain Float64 unless
  the caller permits a measured error tolerance; do not downcast financial
  or research values simply to reduce memory.
- **Exact money with `Decimal`.** `str.to_decimal(scale=2)` (scale is
  keyword-only) avoids binary floating-point representation error. Choose
  precision, scale, overflow behavior, and rounding from the data contract;
  reducing scale can lose fractional digits. Do not add `.round(2)` unless
  the calculation or presentation explicitly calls for it.
- **Prefer native dtypes for computation.** An `Object` column containing
  arbitrary Python objects limits native expression support. Keep data in
  supported native types when possible; isolate object-based fallbacks when
  the required operation cannot be represented natively.

Measure, don't guess:

```python
print(lf.explain())   # did the filter/projection push into the scan?
df.estimated_size()   # in-memory bytes
df.n_chunks()         # fragmentation; many chunks -> rechunk after concat
```

`.profile()` is deprecated as of 1.44 — it timed the old in-memory engine
and misreports under streaming. Use `explain()` to inspect the plan.
