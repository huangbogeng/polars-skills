# Reshaping: Pivot, Unpivot, Concat, Explode

Load this file when a task changes the *shape* of a frame — wide ↔ long,
combining several frames, or expanding/collapsing list columns — rather
than computing new values in place (that is `with_columns` / `select`).

Examples target Polars 1.44.2 and 2.0.0. Older 1.x releases do not necessarily
have `LazyFrame.pivot`, frame-level `gather`, or `horizontal_extend`; inspect
the installed API before using them. For a missing lazy operation, collect
at that boundary and use an equivalent supported API.

## Contents

`pivot` (long → wide, and the DataFrame/LazyFrame signature split);
`unpivot` (wide → long); `transpose`; `pl.concat` (vertical / horizontal /
diagonal / align); `explode` ↔ `implode`; `gather` / `gather_every`.

---

## pivot() — long → wide

One output column per distinct value of `on`; `index` is the row key;
`values` are reduced by `aggregate_function`.

```python
#  ix col val               ix  x  y
#  a  x   1      pivot ->   a  1  2
#  a  y   2                 b  3  4
#  b  x   3
#  b  y   4
df.pivot(on="col", index="ix", values="val", aggregate_function="sum")
```

- `aggregate_function` is **required** as soon as an `(index, on)` cell can
  hold more than one value, otherwise it raises
  `ComputeError: ... expected no or a single value`. Pass `"sum"`, `"mean"`,
  `"first"`, `"len"`, … or an expression. For a cross-tab count use
  `aggregate_function="len"` (empty cells become 0, not null — see
  `pandas-to-polars.md`).
- `sort_columns=True` orders the pivoted columns; `maintain_order=True`
  (the DataFrame default) keeps first-seen row order.
- Multiple `values=["v1", "v2"]` name columns `<value><separator><on>`
  (`v1_x`, `v1_y`, …); set `separator="|"` for `v1|x`.

**LazyFrame.pivot has a different signature.** `on_columns` — the explicit
list of pivot values — is *required* (positional or keyword), and there is *no*
`sort_columns`. `on_columns` also fixes the output column order:

```python
lf.pivot("col", ["y", "x"], index="ix", values="val",
         aggregate_function="sum").collect()          # columns: ix, y, x
```

Pivot is order- and memory-heavy: do it near the end of a chain, on
already-filtered and pre-aggregated data.

## unpivot() — wide → long

Melt value columns into `(variable, value)` rows — the inverse of pivot.

```python
df.unpivot(index="ix")                  # -> ix, variable, value
df.unpivot(on=["x", "y"], index="ix",   # melt only x and y
           variable_name="metric", value_name="amount")
```

`index` columns are kept as-is; `on` selects which columns to melt (default:
everything not in `index`). Rename the two generated columns with
`variable_name` / `value_name` (defaults are `variable` and `value`).

## transpose()

Swap rows and columns; header handling is explicit.

```python
df.transpose()                          # columns -> column_0, column_1, …
df.transpose(include_header=True, header_name="key")  # keep the old names
df.transpose(column_names=["r0", "r1"])              # name the new columns
```

`transpose` performs no aggregation, so ragged or duplicate data can
surprise; prefer `pivot` / `unpivot` whenever there is a key to group on.

## pl.concat() — combine frames

```python
pl.concat([lf_a, lf_b])                         # how="vertical": stack rows
pl.concat([lf_a, lf_b], how="vertical_relaxed") # tolerate dtype differences
pl.concat([lf_a, lf_b], how="horizontal_extend")# side by side (see note)
pl.concat([lf_a, lf_b], how="diagonal")         # union of columns, null-filled
pl.concat([lf_a, lf_b], how="align")            # set-align shared columns
```

- `vertical` requires identical schemas (names, order, dtypes);
  `vertical_relaxed` casts columns to a common supertype.
- `diagonal` stacks frames that have different columns, filling the gaps
  with nulls; `diagonal_relaxed` additionally relaxes dtypes.
- `align` combines frames on the columns they share using set logic rather
  than positional stacking; variants `align_inner` / `align_left` /
  `align_right` / `align_full` control the match.
- **Use `how="horizontal_extend"` to pad shorter frames with nulls.**
  `how="horizontal"` was deprecated in 1.42.1 and requires equal heights
  in 2.0. On Polars 2.0, `strict=False` no longer opts into padding and
  raises; `strict` cannot be combined with `horizontal_extend`. On older
  versions without `horizontal_extend`, verify the behavior of `horizontal`
  rather than assuming the new name exists.
- `rechunk=True` coalesces the chunk layout after a large concat (costs a
  copy — only worth it when many row-wise operations follow).

## explode() ↔ implode()

`explode` emits one row per list element (the frame grows); `implode` is the
inverse — it collects values into a list, and is used inside an aggregation.

```python
lf.explode("tags")              # one row per tag
lf.explode("tags", "scores")    # several list columns, expanded in parallel

lf.group_by("id").agg(pl.col("v").implode())   # values -> list per group
```

A null list keeps its row (`keep_nulls=True`). On Polars 2.0,
`empty_as_null=False` is the default: an empty list produces **zero** rows.
Pass `empty_as_null=True` to restore the 1.x behavior of one null row. Row
counts change wherever a list column contains empty lists.

`unpivot` does not guarantee output order on Polars 2.0 (streaming default);
sort explicitly when order is part of the result. See `version-2.md`.

## gather() / gather_every()

Positional row selection — by index, not by predicate (that is `filter`).

```python
lf.gather([0, 2, 4])                   # specific row indices
lf.gather(pl.Series([1, 3]))
lf.gather(pl.col("idx"))               # indices taken from a column/expr
lf.select(pl.col("v").gather_every(2)) # every 2nd value
lf.gather([0, 99], null_on_oob=True)   # out-of-range -> null, not an error
```

Out-of-range indices raise `OutOfBoundsError` unless `null_on_oob=True`.
