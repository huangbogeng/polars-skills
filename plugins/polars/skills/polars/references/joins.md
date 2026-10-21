# Joins: asof, inequality, validation, fan-out

Load this file for nearest-in-time matching (`join_asof`), non-equi / range
joins (`join_where`), asserting key cardinality (`validate=`), cross
products, or diagnosing a join that changed the row count. The basic
inner / left / semi / anti semantics live in `contexts.md`.

Examples target Polars 1.44.2 and 2.0.0. On older versions, verify
`join_where` availability/strategies and join parameter names;
`nulls_equal` was named `join_nulls` before 1.24.

## Contents

Equi-join knobs (suffix, coalesce, nulls_equal); `validate=` cardinality
assertions; `join_asof` (nearest on an ordered key); `join_where`
(inequality / range); cross join; diagnosing fan-out.

---

## Equi-join knobs

`how` is one of `inner`, `left`, `full`, `semi`, `anti`, `cross`. Beyond the
basics in `contexts.md`:

```python
lf.join(other, left_on="id", right_on="user_id", how="left")
lf.join(other, on="id", how="left", suffix="_r")       # rename right clashes
lf.join(other, left_on="a", right_on="b", how="left", coalesce=False)
lf.join(other, on="id", how="inner", nulls_equal=True) # let null keys match
```

- A non-key column present on both sides gets `suffix` (default `"_right"`).
- `coalesce` decides whether the two key columns are merged into one after
  the join. `left` / `inner` coalesce by default (one key column);
  `coalesce=False` keeps both (`a` and `b`). `full` keeps both by default;
  `coalesce=True` merges them.
- Null keys never match by default; `nulls_equal=True` makes null match
  null (see the join gotcha in `SKILL.md`). The old name `join_nulls` was
  removed in 2.0.
- On Polars 2.0 the lazy API defaults to streaming and does not preserve
  incidental row order. If the result must follow the left or right input,
  pass `maintain_order="left"` / `"right"` (equi-joins) or sort after
  collecting. See `version-2.md`.

## validate= — assert key cardinality

Declare the expected multiplicity and Polars raises instead of silently
fanning out:

```python
lf.join(dim,   on="id", how="left",  validate="m:1")   # right key unique
lf.join(other, on="id", how="inner", validate="1:1")
```

Options: `"m:m"` (default, no check), `"m:1"`, `"1:m"`, `"1:1"`. A violation
raises an error at execution. Reach for
`"m:1"` / `"1:1"` whenever the right side is meant to be a lookup/dimension
table — it turns a silent row-count blow-up into an immediate error.

## join_asof — nearest match on an ordered key

"The last known value at or before each row" — prices, sessions,
point-in-time lookups. Both frames **must be sorted** by the `on` key, or it
raises `InvalidOperationError: ... is not sorted`.

```python
trades.sort("ts").join_asof(quotes.sort("ts"), on="ts", strategy="backward")
```

- `strategy`: `"backward"` (default — last right row `<=` the left key),
  `"forward"` (next right row `>=`), `"nearest"`.
- `tolerance` drops matches farther away than a gap. Pass a duration string
  (`"1d"`, `"5m"`) or a `datetime.timedelta` — **not** `pl.duration(...)`.
- `by="sym"` runs a separate asof within each group; use `by_left` /
  `by_right` when the grouping column is named differently per side. With
  `by=`, Polars cannot verify sortedness for you, so sort each group.
- Different key names: `left_on=` / `right_on=`.

```python
trades.sort("ts").join_asof(
    quotes.sort("ts"), on="ts", strategy="backward", tolerance="1s", by="sym"
)
```

## join_where — inequality / non-equi / range joins

Match on an arbitrary predicate instead of key equality: overlapping
intervals, thresholds, "the first event after X".

```python
events.join_where(                      # events falling inside a window
    windows,
    pl.col("ts") >= pl.col("start"),
    pl.col("ts") <  pl.col("end"),
)
```

Multiple predicates are AND-ed. `how="inner"` (default) or `"left"`; a
`suffix` renames right-side clashes. There is no `on=` — every condition is
an expression over both frames — so the right key is not deduplicated and
the result is many-to-many by construction.

## Cross join

Cartesian product. There is no `cross_join` method on a LazyFrame:

```python
lf.join(other, how="cross")             # height(lf) * height(other) rows
```

Use it deliberately (e.g. broadcasting a scalar threshold); it multiplies
rows.

## Diagnosing fan-out

For an inner/left equi-join, more rows out than the left side had in means
matched right keys were not unique. Full, cross, and inequality joins can
grow for other reasons. Check the relevant key cardinality:

```python
left.height                                    # before
joined.height                                  # after — larger == fan-out
other.select(pl.col("id").n_unique()).collect()  # vs. other's row count
```

If uniqueness is required, assert it with `validate="m:1"`. Aggregate or
deduplicate only when the task defines how multiple matches should be
resolved; otherwise preserve the intended many-to-many result.
