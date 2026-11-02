# SQL

Load this file when the task uses Polars SQL (`pl.sql`, `SQLContext`,
`DataFrame.sql`, `LazyFrame.sql`) or translates SQL into Polars expressions.
Examples target Polars 1.44.2, 2.0.0, and 2.1.0; 2.1 additions are marked.

## Contents

Context and registration; `pl.sql` caller scope; lazy execution; mixing SQL
and expressions; CTEs and recursive CTEs; temp tables and CREATE; ASOF
joins; subqueries; table functions; scalar functions and time; dialect
notes; types and casts; qualified names; Polars 1.x; gotchas; reference map.

## Context and registration

Use `SQLContext` for explicit, local table registration. `pl.sql` resolves
frames from the caller's namespace (locals and globals); `DataFrame.sql` /
`LazyFrame.sql` query the frame as `self`.

```python
orders = pl.LazyFrame({
    "region": ["east", "west", "east"],
    "amount": [10, 20, 5],
})
ctx = pl.SQLContext(orders=orders)

result = ctx.execute(
    """
    SELECT region, SUM(amount) AS total
    FROM orders
    GROUP BY region
    ORDER BY total DESC
    """
)
assert isinstance(result, pl.LazyFrame)
assert result.collect().to_dicts() == [
    {"region": "west", "total": 20},
    {"region": "east", "total": 15},
]
```

`SQLContext(frames={...})`, keyword frames, and `register_globals=True` are
alternatives. `register` / `register_many` add tables later; `unregister`
removes them. A `SQLContext` sees only the tables registered in it; prefer it
for reusable pipelines, catalog work, and cases where scope is unclear.
`pl.sql` is convenient for a quick query over frames in the current scope.

## pl.sql and caller scope

`pl.sql` resolves frames visible in the calling scope, including locals.
A different function or module does not share that namespace.

```python
def query_local():
    local = pl.LazyFrame({"a": [1, 2]})
    return pl.sql("SELECT * FROM local ORDER BY a").collect()

assert query_local()["a"].to_list() == [1, 2]
```

## Lazy execution

SQL is translated to expressions and executed by the normal Polars engine.
`execute()` returns a `LazyFrame`; pass `eager=True` to `SQLContext(...)` or
`execute()` to collect immediately.

```python
ctx = pl.SQLContext(t=pl.LazyFrame({"a": [1, 2, 3]}))
lazy = ctx.execute("SELECT * FROM t WHERE a > 1")
assert isinstance(lazy, pl.LazyFrame)
assert lazy.collect()["a"].to_list() == [2, 3]
```

On Polars 2.x, SQL collection uses the streaming engine like any other lazy
query, so row order is not guaranteed unless the query defines it. Add
`ORDER BY` when order is part of the result. Parsing and name resolution are
deferred to `collect()` / `collect_schema()` (2.0), so an unknown table or
column can raise late.

## Mixing SQL and expressions

A SQL result is an ordinary `LazyFrame`; continue with expressions where
that is clearer, and the chain still optimizes as one plan. `pl.sql_expr`
embeds one SQL expression inside `select()` or `with_columns()`.

```python
df = pl.DataFrame({"price": [10, 20], "qty": [2, 3]})
result = df.select(pl.sql_expr("price * qty AS total"))
assert result["total"].to_list() == [20, 60]
```

`pl.sql_expr` accepts one string or a sequence and returns an `Expr` or a
list of expressions. Prefer native expressions when they are equally clear.

## CTEs and recursive CTEs

A CTE is a named subquery; recursive CTEs arrived in 2.1.

```python
ctx = pl.SQLContext()
result = ctx.execute(
    """
    WITH RECURSIVE seq(n) AS (
        SELECT 1
        UNION ALL
        SELECT n + 1 FROM seq WHERE n < 5
    )
    SELECT n FROM seq
    """
).collect()
assert result["n"].to_list() == [1, 2, 3, 4, 5]
```

Without the `RECURSIVE` keyword, a CTE cannot reference itself. CTEs and
registered tables take precedence over catalog tables.

## Temp tables and CREATE

`CREATE TABLE name AS SELECT ...` registers a table in the context (1.x).
2.1 adds `CREATE TEMP TABLE` and `CREATE OR REPLACE TEMP TABLE`; `TEMP`
always creates a context table, even when an Iceberg catalog is attached.

```python
ctx = pl.SQLContext(t=pl.LazyFrame({"a": [1, 2, 3]}))
ctx.execute(
    "CREATE OR REPLACE TEMP TABLE gt1 AS SELECT * FROM t WHERE a > 1"
)
result = ctx.execute("SELECT * FROM gt1 ORDER BY a").collect()
assert result["a"].to_list() == [2, 3]
```

`execute()` accepts one statement at a time: run `CREATE` and `SELECT` as
separate calls. `DROP TABLE [IF EXISTS]` and `TRUNCATE TABLE [IF EXISTS]`
are supported; `INSERT`, `UPDATE`, and `DELETE` are not yet supported on
registered tables. Catalog DDL is covered in `iceberg.md`.

## ASOF joins

2.1 adds `ASOF JOIN` with `MATCH_CONDITION`. The clause order is
`MATCH_CONDITION (...)` before `ON ...`; both inputs must be sorted by the
match key.

```python
trades = pl.LazyFrame({"sym": ["a", "a"], "ts": [1, 3], "px": [10, 30]})
quotes = pl.LazyFrame({"sym": ["a", "a"], "ts": [2, 4], "qx": [20, 40]})
ctx = pl.SQLContext(trades=trades, quotes=quotes)

result = ctx.execute(
    """
    SELECT trades.sym, trades.ts, trades.px, quotes.qx
    FROM trades
    ASOF JOIN quotes
      MATCH_CONDITION (trades.ts >= quotes.ts)
      ON trades.sym = quotes.sym
    """
).collect()
assert result["qx"].to_list() == [None, 20]
```

`MATCH_CONDITION` is the inequality; `ON` supplies equality keys. Selecting
only the needed columns avoids the suffixed duplicate key columns.

## Subqueries

2.1 supports uncorrelated subqueries in `JOIN ... ON` and correlated scalar
subqueries, including without an aggregate.

```python
ctx = pl.SQLContext(t=pl.LazyFrame({"g": ["a", "a", "b"], "v": [1, 3, 2]}))
result = ctx.execute(
    """
    SELECT t.g, t.v,
           (SELECT MAX(v) FROM t AS x WHERE x.g = t.g) AS group_max
    FROM t
    ORDER BY t.g, t.v
    """
).collect()
assert result["group_max"].to_list() == [3, 3, 2]
```

A correlated subquery references the outer relation by alias; without an
alias, an inner name can shadow the outer one. Prefer an explicit alias on
both sides.

## Table functions

Read files directly in `FROM`: `read_csv`, `read_parquet`, `read_ndjson`,
and `read_ipc`. 2.1 adds `read_ndjson`, named options (`name => value`), and
lists of paths or globs.

```python
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    first = Path(tmp) / "a.ndjson"
    second = Path(tmp) / "b.ndjson"
    first.write_text('{"x": 1}\n')
    second.write_text('{"x": 2}\n')
    ctx = pl.SQLContext()
    result = ctx.execute(
        f"SELECT * FROM read_ndjson(['{first}', '{second}']) ORDER BY x"
    ).collect()
assert result["x"].to_list() == [1, 2]
```

Named options use the same names as the Python scan API:

```
SELECT *
FROM read_csv('data.csv', null_values => ['N/A'], try_parse_dates => true)
```

Quote paths as SQL strings; a list is written `read_parquet(['a', 'b'])`.

## Scalar functions and time

2.1 adds `generate_series`, `range`, `REGEXP_REPLACE`, `DATE_TRUNC`,
`CURRENT_DATE`, `CURRENT_TIME`, `CURRENT_TIMESTAMP`, and time-zone-aware
timestamps.

```python
ctx = pl.SQLContext()
result = ctx.execute(
    """
    SELECT
        DATE_TRUNC('day', TIMESTAMP '2024-01-02 03:04:05') AS day,
        REGEXP_REPLACE('a-b-c', '-', '_') AS first_replace,
        REGEXP_REPLACE('a-b-c', '-', '_', 'g') AS all_replace,
        CURRENT_DATE AS today
    """
).collect()
assert result["first_replace"].to_list() == ["a_b-c"]
assert result["all_replace"].to_list() == ["a_b_c"]
assert result["today"].null_count() == 0

series = ctx.execute("SELECT generate_series(1, 3) AS n").collect()
assert series["n"].to_list() == [[1, 2, 3]]
ranged = ctx.execute("SELECT * FROM range(1, 4)").collect()
assert ranged["range"].to_list() == [1, 2, 3]
```

`REGEXP_REPLACE` replaces the first match by default; pass the `'g'` flag to
replace all. `generate_series` returns a list column in `SELECT`, while
`range` used in `FROM` produces rows. Time-zone-aware values keep a `UTC`
(or named) zone:

```python
ctx = pl.SQLContext()
result = ctx.execute(
    "SELECT TIMESTAMPTZ '2024-01-01 00:00:00+00' AS ts"
).collect()
assert result.schema["ts"] == pl.Datetime("us", "UTC")
```

## Dialect notes

Polars SQL follows PostgreSQL loosely. Current 2.x differences:

- Exact numeric literals without an exponent are `Decimal`; use
  `1.1::DOUBLE` or `1.1e0` for `Float64`.
- Integer `/` returns `Float64` (`5 / 2` -> `2.5`); `DIV(5, 2)` does integer
  division. `%` and `DIV` truncate toward zero.
- 2.1 adds `DATE_TRUNC`; on 2.0 use `DATE_PART` / `EXTRACT` or expressions.
- `REGEXP_REPLACE` replaces the first match by default; pass `'g'` to replace
  all matches.
- Errors surface at `collect_schema()` / `collect()`, not at `execute()`
  (2.0+).

```python
ctx = pl.SQLContext()
result = ctx.execute(
    "SELECT 5 / 2 AS ratio, DIV(5, 2) AS whole, 1.1 AS exact"
).collect()
assert result["ratio"].to_list() == [2.5]
assert result["whole"].to_list() == [2]
assert str(result.schema["exact"]).startswith("Decimal")
```

## Types and casts

2.1 adds `STRUCT<...>` and `MAP(...)` type names. Struct casts match fields
by name; `TRY_CAST` tolerates missing fields and failed conversions.

```python
df = pl.DataFrame({"s": [{"x": 1, "y": "a"}]})
result = df.sql(
    "SELECT TRY_CAST(s AS STRUCT<y VARCHAR, x BIGINT, z DOUBLE>) AS s "
    "FROM self"
)
assert result.schema["s"] == pl.Struct({
    "y": pl.String, "x": pl.Int64, "z": pl.Float64,
})
```

Use angle brackets for struct fields, not parentheses. `MAP(KEY, VALUE)` is
valid in `CREATE TABLE` column definitions; casting to `MAP` only accepts a
`MAP` value or null:

```python
ctx = pl.SQLContext()
ctx.execute("CREATE TEMP TABLE kv (m MAP(VARCHAR, INTEGER))")
```

The compact PostgreSQL cast syntax also works: `col::BIGINT`, `col::DATE`.

## Qualified names

2.1 supports qualified table names and qualified column references. A
column can use any trailing part of a catalog table's full name.

```python
ctx = pl.SQLContext(t=pl.LazyFrame({"a": [1, 2]}))
result = ctx.execute("SELECT t.a FROM t ORDER BY t.a").collect()
assert result["a"].to_list() == [1, 2]
```

For an attached catalog `lake`, `lake.ns.t.a`, `ns.t.a`, and `t.a` all work
as column qualifiers when the table is unambiguous.

## On Polars 1.x

This reference targets 2.x; check `pl.__version__`. On 1.44.2:

- SQL numeric literals are `Float64` (`price * 1.1` is `Float64`), not
  `Decimal`.
- SQL errors raise when the query is built, not at `collect()`.
- The 2.1 additions above are unavailable: recursive CTEs, temp tables,
  `ASOF JOIN ... MATCH_CONDITION`, `generate_series` / `range`,
  `DATE_TRUNC`, `CURRENT_*`, time-zone-aware literals, `STRUCT<...>` /
  `MAP(...)` type names, and `read_ndjson` table functions.
- `collect(engine="streaming")` is opt-in; 2.x lazy queries default to it.

## Gotchas

- **One statement per `execute()`.** A string containing `CREATE ...; SELECT
  ...` raises `SQLInterfaceError`; run each statement separately.
- **Resolution is lazy (2.0).** Unknown tables/columns may raise at
  `collect()` or `collect_schema()`, not at `execute()`.
- **Streaming order (2.x).** Add `ORDER BY` when the consumer depends on row
  order; the streaming engine does not guarantee incidental order.
- **Numeric literals are exact (2.0).** SQL exact numeric literals are
  `Decimal`; `%` and `DIV` truncate toward zero. Cast the result when
  Python-side float behavior is required.
- **ASOF clause order.** `MATCH_CONDITION (...)` comes before `ON ...`, and
  both inputs must be sorted on the match key.
- **`pl.sql` uses the caller's namespace.** It resolves locals and globals
  in the calling scope; a frame from another function or module may not be
  visible. Register explicitly with `SQLContext` when scope is unclear.
- **Catalog identifiers are case-sensitive** and serialized plans can
  contain credentials. See `iceberg.md`.

## Reference map

Fetch from `https://docs.pola.rs/api/python/stable/reference/sql/`:

| Need | Page |
|---|---|
| `SELECT`, `JOIN`, `GROUP BY`, `QUALIFY`, `ORDER BY`, `LIMIT` | `clauses.html` |
| `UNION`, `EXCEPT`, `INTERSECT` | `set_operations.html` |
| `CREATE TABLE`, `EXPLAIN`, `SHOW TABLES`, `UNNEST` | `table_operations.html` |
| Aggregate, array, math, string, temporal, window functions | `functions/<category>.html` |
| Operators | `operators/index.html` |
| `pl.sql`, `SQLContext`, `pl.sql_expr` | `python_api.html` |
