# From Question to Query: Insight Recipes

Query shapes for answering natural language questions about data. Each
recipe is a complete lazy chain: adapt column names, collect once. Follow the
workflow in `SKILL.md` — schema first, state how you read vague terms, answer
with numbers.

Throughout, `lf` is a LazyFrame, e.g.
`lf = pl.scan_csv("data.csv", try_parse_dates=True, null_values=["N/A", ""])`.

## Contents

Top N by Y; top N per group; change vs. previous period; share of total;
distribution; trend over time (resampling); trailing/sliding windows;
conditional breakdown; above/below group average; distinct counts; sanity
checks before presenting an answer.

---

## "What are the top N X by Y?"

```python
(
    lf.group_by("product")
    .agg(pl.col("revenue").sum().alias("total_revenue"))
    .top_k(10, by="total_revenue")
    .sort("total_revenue", descending=True)
    .collect()
)
```

`top_k` avoids sorting the whole frame; the final sort is presentational.

## "Top N per group" (top 3 products per region)

```python
(
    lf.group_by("region", "product")
    .agg(pl.col("revenue").sum().alias("total"))
    .sort("region", "total", "product", descending=[False, True, False],
          nulls_last=True)
    .group_by("region", maintain_order=True)
    .head(3)
    .collect()
)
```

This returns at most three products per region, breaking revenue ties by
product name. If the user wants all products in the top three distinct
revenue levels, use dense rank instead and explain that it can exceed three
rows. Apply a deterministic tie-breaker to global top-k too when required.

## "How did X change vs. the previous period?"

Match the previous **calendar month**, leaving growth null for a missing,
null, or zero previous value:

```python
monthly = (
    lf.group_by(pl.col("date").dt.truncate("1mo").alias("month"))
    .agg(pl.col("revenue").sum().alias("revenue"))
)
previous = monthly.select(
    pl.col("month").dt.offset_by("1mo"),
    pl.col("revenue").alias("prev"),
)
(
    monthly.join(previous, on="month", how="left", validate="1:1")
    .with_columns(
        growth_pct=pl.when(pl.col("prev").is_not_null() & (pl.col("prev") != 0))
        .then((pl.col("revenue") / pl.col("prev") - 1) * 100)
        .otherwise(None)
    )
    .sort("month")
    .collect()
)
```

For per-region growth, include `region` in the aggregation, in `previous`,
and in the join keys. A plain `shift(1)` compares consecutive observed rows,
which can skip missing months. Do not fill a missing month with zero unless
absence means zero in the data contract.

`sum()` over a **typed** column returns `0` for a group whose values are
all null (and for an `expr.filter(...)` subset with no matches), but
`null` when the column's dtype is `Null`. A column created from an
all-`None` Python list gets dtype `Null`, so `sum()` there stays `null`.
Before you present a total, decide which case means "unknown" for the
contract: an unknown month summed by `sum()` becomes `0`, and feeding
that to the growth formula reports `-100%` rather than missing data. Use
`.is_null().all()` on the source values, or validate the monthly totals
against the expected calendar, when unknown must stay distinct from zero.

## "What share does each X have of the total?"

```python
(
    lf.group_by("category")
    .agg(pl.col("revenue").sum().alias("revenue"))
    .with_columns(
        share_pct=pl.when(pl.col("revenue").sum() != 0)
        .then(pl.col("revenue") / pl.col("revenue").sum() * 100)
        .otherwise(None)
    )
    .sort("share_pct", descending=True)
    .collect()
)
```

After `agg`, `pl.col("revenue").sum()` is the grand total; the division
broadcasts. The same trick works pre-aggregation with
`.sum().over("region")` for share-within-group.

## "What does the distribution of X look like?"

```python
# Five-number-style summary per group
(
    lf.group_by("segment")
    .agg(
        pl.col("order_value").min().alias("min"),
        pl.col("order_value").quantile(0.25).alias("p25"),
        pl.col("order_value").median().alias("median"),
        pl.col("order_value").quantile(0.75).alias("p75"),
        pl.col("order_value").max().alias("max"),
        pl.len().alias("n"),
    )
    .collect()
)
```

Quantile bins on **Polars 1.44.2**:

```python
(
    lf.with_columns(
        bucket=pl.col("order_value").qcut(4, allow_duplicates=True)
    )
    .group_by("bucket")
    .agg(pl.len().alias("n"), pl.col("order_value").mean().alias("avg"))
    .sort("bucket")
    .collect()
)
```

`qcut` can produce duplicate quantile boundaries on tied data. This example
allows fewer bins and keeps interval labels; it does not promise four
equally sized groups. Choose another tie policy if the task requires one.

For new **Polars 2.0** code, use `bin_quantiles`; `qcut` is deprecated:

```python
(
    lf.with_columns(bucket=pl.col("order_value").bin_quantiles(4, labels=False))
    .group_by("bucket")
    .agg(pl.len().alias("n"), pl.col("order_value").mean().alias("avg"))
    .sort("bucket")
    .collect()
)
```

`bin_quantiles` keeps equal values together and can leave bins empty;
`labels=False` returns integer bin IDs. Its breakpoints use order statistics
and its intervals are left-closed by default, while legacy `qcut` uses
interpolated quantiles and right-closed intervals. They are not equivalent
replacements in a migration that must preserve exact group membership.

## "How does X trend over time?" (resampling)

```python
(
    lf.sort("ts")
    .group_by_dynamic("ts", every="1w")
    .agg(
        pl.col("revenue").sum().alias("revenue"),
        pl.col("order_id").n_unique().alias("orders"),
    )
    .collect()
)
```

`group_by_dynamic` requires a sorted time column. With `period=every` and
`closed="left"`, it collapses time into non-overlapping buckets. `every`
accepts durations like `"1d"`,
`"1w"`, `"1mo"`, `"1q"`, `"1y"`; add `group_by=` for one series per category.
Tune windows with `period=` (bucket width, defaults to `every`), `offset=`,
`closed="left"` and `label="left"` (both default to `"left"`), and
`include_boundaries=True` to emit `_lower_boundary`/`_upper_boundary`.
Other `period`/`closed` choices can overlap windows; empty windows are not
automatically emitted.

## "What is the trailing N-day value?" (sliding windows)

Resampling groups time into buckets; a *sliding* window keeps one row per
input and aggregates the trailing span — what "7-day trailing average" or
"running 30-day sum" means. Two forms with the same trailing-mean values on
the tested versions (the expression form also keeps the input columns):

```python
# window groups: one row per input row, window ending at that row
(
    lf.sort("ts")
    .rolling("ts", period="7d")
    .agg(pl.col("revenue").mean().alias("rev_7d"))
    .collect()
)

# expression form: same result, usable inside with_columns
lf.sort("ts").with_columns(
    pl.col("revenue").rolling_mean_by("ts", "7d").alias("rev_7d")
).collect()
```

- `.rolling(index, period=...)` needs a sorted temporal key; add
  `group_by="sym"` for one window per category. `closed="right"` (default)
  includes the current row's timestamp.
- Duration windows on **irregular** timestamps: `rolling_*_by(col, window)`
  for `mean`, `sum`, `min`, `max`, `std`, `var`, `quantile` — `quantile` is
  keyword-only (`rolling_quantile_by("ts", "7d", quantile=0.9)`).
  `min_samples=` controls how many leading rows stay null.
- Weighted decay instead of a flat window: `ewm_mean_by("ts",
  half_life="2d")` (half-life is keyword-only).
- For a fixed **row count** (last N rows, not N days) drop the `_by`:
  `rolling_mean(window_size=N)`, with `center=True` to center the window.

## "Break X down by whether/which ..." (conditional breakdown)

```python
(
    lf.group_by("region")
    .agg(
        pl.col("amount").filter(pl.col("status") == "paid")
            .sum().alias("paid"),
        pl.col("amount").filter(pl.col("status") == "pending")
            .sum().alias("pending"),
        (pl.col("status") == "refunded").sum().alias("refund_count"),
    )
    .collect()
)
```

`expr.filter()` inside `agg` aggregates a subset per group; summing a boolean
counts how often it is true. When no row in a group satisfies the predicate
or every value is null, the filtered `sum()` is `0`, not `null` — see the
growth recipe.

## "Which X are above/below their group average?" (window)

```python
(
    lf.with_columns(
        dept_avg=pl.col("salary").mean().over("department"),
    )
    .filter(pl.col("salary") > pl.col("dept_avg"))
    .select("name", "department", "salary", "dept_avg")
    .collect()
)
```

## "How many distinct X ...?" / quick counts

```python
(
    lf.group_by("region")
    .agg(
        pl.len().alias("rows"),
        pl.col("customer_id").n_unique().alias("customers"),
        pl.col("order_id").null_count().alias("missing_orders"),
    )
    .collect()
)
```

`pl.len()` counts rows including nulls; `.count()` counts non-null values of
a column. `n_unique()` includes null as a distinct value; use
`.drop_nulls().n_unique()` when only known IDs should count.

---

## Sanity checks before presenting an answer

Cheap checks that catch most wrong answers:

```python
result = query.collect()

result.height                      # plausible number of rows?
result.null_count()                # unexpected nulls from a join or cast?
lf.select(pl.len()).collect()      # input row count for a gut check
```

If an inner/left equi-join produced more rows than the left input, matched
keys were not unique on the right. Other joins can grow for different
reasons. If an aggregate is null, the source column may have
contained only nulls after filtering, or a cast with `strict=False`
nulled unparsable values; check `lf.head().collect()` for dirty data and
move cleaning into the scan (`null_values=[...]`).
