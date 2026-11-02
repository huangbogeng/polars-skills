# Testing and Validation

Load this file when writing tests for Polars code or validating a result
before answering.

## Contents

What to assert; `polars.testing` asserts; tolerances and ordering; schema and
plan checks; batch processing; property-based tests; references.

## What to assert

A useful assertion covers more than values:

- values, including nulls and boundary rows;
- dtypes and column names;
- column order when it is part of the contract;
- row order when the consumer depends on it;
- grouping, join, and window boundaries (ties, missing periods, zero
  denominators).

## `polars.testing` asserts

`polars.testing` is not imported by default; import the helpers explicitly.
It gives better diffs than comparing `to_dicts()`.

```python
from polars.testing import assert_frame_equal, assert_series_equal

result = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
expected = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
assert_frame_equal(result, expected)
assert_series_equal(result["a"], expected["a"])
```

- `check_row_order=False` when order is not part of the contract.
- `check_column_order=False` when names matter but order does not.
- `check_dtypes=False` only when a deliberate dtype difference is allowed.
- `rel_tol` / `abs_tol` control float comparisons; `check_exact=True` is
  rarely what you want for floats.
- `categorical_as_str=True` compares categorical values by string.
- `assert_schema_equal`, `assert_frame_not_equal`, and
  `assert_series_not_equal` cover the other directions.

`DataFrame.equals(other, null_equal=True)` is a quick boolean check; it does
not produce a test diff. Use `assert_frame_equal` in tests.

## Schema and plan checks

`collect_schema()` validates names and dtypes without collecting the result.
It may still read file metadata or sample a CSV, so do not claim it is free.

```python
lf = pl.LazyFrame({"a": [1, 2], "b": ["x", "y"]})
schema = lf.collect_schema()
assert schema.names() == ["a", "b"]
assert schema["a"] == pl.Int64

plan = lf.filter(pl.col("a") > 1).explain()
assert "SELECTION" in plan or "FILTER" in plan
```

Plan text is version-dependent; assert on stable fragments such as
`SELECTION` or `PROJECT`, not the full plan.

## Batch processing

`collect_batches()` streams results as `DataFrame` chunks. It is unstable and
slower than native sinks; use it only when a sink cannot express the logic.

```python
lf = pl.LazyFrame({"a": [1, 2, 3]})
sizes = [batch.height for batch in lf.collect_batches(chunk_size=2)]
assert sum(sizes) == 3
```

`sink_batches(function, chunk_size=...)` calls a function per chunk and can
return a `LazyFrame` for lazy execution. Check every chunk; the query runs
fully unless stopped.

## Property-based tests

`polars.testing.parametric` provides Hypothesis strategies:

```python
from hypothesis import given
from polars.testing.parametric import dataframes

@given(dataframes(cols=3, allow_null=True))
def test_numeric(df):
    assert df.height >= 0
```

Use `load_profile` / `set_profile` and `POLARS_HYPOTHESIS_PROFILE` to control
the number of examples. This is optional; do not add Hypothesis to a project
that does not already use it.

## References

- Testing API: https://docs.pola.rs/api/python/stable/reference/testing.html
- `assert_frame_equal`: https://docs.pola.rs/api/python/stable/reference/api/polars.testing.assert_frame_equal.html
- `collect_batches`: https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.collect_batches.html
- `sink_batches`: https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.sink_batches.html
