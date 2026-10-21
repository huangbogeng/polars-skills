# String, Temporal, List, Struct Expressions

Load this file when a task involves text processing, date/time arithmetic,
nested data, or multi-type column selection.

## Contents

Finding & verifying expressions in the API reference; `str.*` (includes
parsing strings to dates/datetimes); `dt.*` (includes window grouping on
timestamps); `list.*`; `struct.*`; expression expansion & selectors;
casting; `when / then / otherwise`.

## Finding & verifying expressions in the API reference

Examples target Polars 1.44.2 and 2.0.0. Verify uncertain methods against
the project's installed version using `help()`, `inspect.signature()`, or
matching-version official documentation.

**Prefer the bundled lookup** for the installed version, with no MCP or
network: `python scripts/polars_api.py doc Expr.str.contains` (also
`search <term>` and `browse Expr.str`). It introspects the interpreter that
runs it, so use the project's environment. If `polars-mcp` is installed,
`polars_search_api("keyword")` / `polars_get_docstring(...)` expose the same
information. Otherwise use local introspection or a browser/fetch tool. For
the latest release, the expression reference is
`https://docs.pola.rs/api/python/stable/reference/expressions/<slug>.html`
(`stable` follows the latest release, not all 1.x versions). Select a version
matching the project and retrieve the relevant method rather than an entire
namespace. The category map below routes to the appropriate section.

### Category → URL map

| Need | Slug |
|---|---|
| `sum`, `mean`, `min`, `max`, `min_by`, `max_by`, `std`, `var`, `count`, `first`, `last`, `quantile` | `aggregation` |
| `arr.*` — fixed-width Array dtype | `array` |
| `bin.*` — encode / decode / contains | `binary` |
| `is_between`, `is_in`, `is_duplicated`, `is_unique`, `any`, `all`, `not_` | `boolean` |
| `cat.*` — categoricals | `categories` |
| `alias`, `exclude`, column references | `columns` |
| `abs`, `log`, `sqrt`, trig, `rank`, `rolling_*`, `cum_*`, `diff`, `pct_change`, `hist`, `rolling_mean_by`/`rolling_sum_by`/`rolling_*_by` (rolling on irregular timestamps), `ewm_mean_by`, `replace`, `replace_strict` | `computation` |
| `ext.*` — custom extension types | `extension` |
| `pl.when`, `pl.lit`, `pl.col`, `pl.coalesce`, `pl.all_horizontal`, `pl.sum_horizontal`, `pl.concat_str`, `pl.int_range`, `pl.struct`, `pl.date_range`, `pl.datetime_range`, `pl.date_ranges`, `pl.datetime_ranges`, `pl.arg_sort_by`, `pl.business_day_count`, `pl.sql_expr` | `functions` |
| `list.*` | `list` |
| `filter`, `sort`, `head`/`tail`/`slice`, `gather`, `gather_every`, `shift`, `fill_null`, `cast`, `over`, `top_k`, `top_k_by`, `bottom_k_by` | `modify_select` |
| `meta.*` — expression introspection | `meta` |
| Misc helpers | `miscellaneous` |
| `name.prefix`, `name.suffix`, `name.to_lowercase`, `name.map`, `name.keep` | `name` |
| Arithmetic / comparison / logical operators | `operators` |
| `str.*` | `string` |
| `struct.*` | `struct` |
| `dt.*` | `temporal` |

---

## String — `str.*`

```python
pl.col("text").str.contains("pattern")          # regex by default
pl.col("text").str.contains("prefix", literal=True)  # literal match
pl.col("text").str.starts_with("pre")
pl.col("text").str.ends_with("suf")
pl.col("text").str.len_chars()                  # character count (not bytes)
pl.col("text").str.len_bytes()                  # byte count

pl.col("text").str.to_lowercase()
pl.col("text").str.to_uppercase()
pl.col("text").str.strip_chars()                # whitespace, both sides
pl.col("text").str.strip_chars_start()          # also: strip_chars_end()
pl.col("text").str.strip_chars(" ,-")           # strip specific characters

pl.col("text").str.replace("old", "new", literal=True)  # first literal match
pl.col("text").str.replace("pat", "new", n=2)   # first TWO regex matches
pl.col("text").str.replace_all("old", "new")    # all matches
pl.col("text").str.replace_all(r"\s+", " ")     # regex

pl.col("text").str.slice(0, 5)                  # chars 0..5
pl.col("text").str.head(3)                      # first 3 chars (also: tail)

pl.col("text").str.split(",")                   # List[Str]
pl.col("text").str.split_exact(",", n=2)        # fixed-width struct
pl.col("text").str.splitn(",", n=3)             # at most n parts

pl.col("text").str.extract(r"(\d+)", group_index=1)   # first capture group
pl.col("text").str.extract_all(r"\d+")                # List[Str] of all matches

pl.col("text").str.zfill(5)                     # zero-pad to width 5
pl.col("text").str.pad_start(8, "0")            # left-pad (also: pad_end)

pl.col("text").str.to_integer(base=10, strict=False)  # parse to Int64
pl.col("text").str.to_decimal(scale=2)                # parse to Decimal (scale = decimal places)
```

### Replace substrings

Patterns are regexes by default. `n` limits the number of replacements; it
does not select the nth match. Use `literal=True` for literal punctuation:

```python
lf.select(
    pl.col("text").str.replace(".", "X").alias("regex"),
    pl.col("text").str.replace(".", "X", literal=True).alias("literal"),
    pl.col("text").str.replace("a", "X", n=2).alias("first_two"),
).collect()
```

### Parse strings to dates and datetimes

```python
# ISO strings like "2024-01-15"
pl.col("d").str.to_date()

# ISO datetimes like "2024-01-15T09:30:00"
pl.col("d").str.to_datetime()

# Custom format — pandas directives carry over, except %f (microseconds),
# which becomes %.f in Polars (%.f parses ".123456" correctly)
pl.col("d").str.to_datetime("%Y-%m-%d %.f")

# Strings contain time but you want date precision: parse then truncate
pl.col("d").str.to_datetime().dt.truncate("1d")

# Non-strict parse: null on failure instead of raising
pl.col("d").str.to_date(strict=False)
```

---

## Temporal — `dt.*`

```python
# Extract components
pl.col("ts").dt.year()
pl.col("ts").dt.month()         # 1-12
pl.col("ts").dt.day()           # 1-31
pl.col("ts").dt.hour()          # also: minute(), second(), microsecond()
pl.col("ts").dt.weekday()       # 1=Monday, 7=Sunday
pl.col("ts").dt.week()          # ISO week number
pl.col("ts").dt.ordinal_day()   # day of year (1-366)
pl.col("ts").dt.quarter()       # 1-4

# Round and truncate
pl.col("ts").dt.truncate("1mo")   # floor to month start; any duration
                                  # string works: "1d", "1h", "15m"
pl.col("ts").dt.round("1h")       # round to nearest hour

# Shift by duration
pl.col("ts").dt.offset_by("1mo")    # add 1 calendar month
pl.col("ts").dt.offset_by("-7d")    # subtract 7 days
pl.col("ts").dt.offset_by("2h30m")  # add 2h 30min

# Format as string
pl.col("ts").dt.strftime("%Y-%m")     # e.g. "2024-03"

# Time zone
pl.col("ts").dt.replace_time_zone("UTC")               # attach tz (naive -> aware)
pl.col("ts").dt.convert_time_zone("Europe/Amsterdam")  # convert between tz

# Type conversions
pl.col("ts").dt.date()      # Datetime -> Date
pl.col("ts").dt.time()      # Datetime -> Time
pl.col("ts").dt.epoch(time_unit="s")   # seconds since Unix epoch

# Duration arithmetic
pl.col("ts") + pl.duration(days=7)
pl.col("end") - pl.col("start")           # Duration column
(pl.col("end") - pl.col("start")).dt.total_seconds()  # integer; also
                                    # total_minutes, total_hours, total_days
```

### Window grouping on timestamps

```python
# Group events into 5-minute buckets
lf.with_columns(
    bucket=pl.col("ts").dt.truncate("5m")
).group_by("bucket").agg(pl.len().alias("events"))

# Window expression using truncated ts as the over() key
lf.with_columns(
    bucket_total=pl.col("amount").sum().over(pl.col("ts").dt.truncate("1d"))
)
```

---

## List — `list.*`

List columns hold a variable-length list per row.

```python
pl.col("tags").list.len()                   # length of each list
pl.col("tags").list.first()                 # also: last(), get(2)
pl.col("tags").list.slice(1, 3)             # sublist [1, 2, 3]
pl.col("tags").list.head(2)                 # first 2 (also: tail)

pl.col("nums").list.sum()                   # also: mean, min, max

pl.col("tags").list.contains("python")      # Boolean, one per row
pl.col("tags").list.sort(descending=True)   # also: sort(), reverse()
pl.col("tags").list.unique()                # order not preserved

pl.col("a").list.concat(pl.col("b"))        # concatenate two list columns
pl.col("tags").list.join(", ")              # join into a single string

# Explode: one row per list element
lf.explode("tags")

# Apply expression to each element without leaving the engine
pl.col("scores").list.eval(pl.element() * 2)
pl.col("scores").list.eval(pl.element().filter(pl.element() > 5))
```

---

## Struct — `struct.*`

Struct columns hold named fields per row, similar to a JSON object column.

```python
# Access a field
pl.col("address").struct.field("city")
pl.col("address").struct.field("zip_code")

# Rename fields
pl.col("address").struct.rename_fields(["town", "postcode"])

# Unnest: promote all struct fields to top-level columns
lf.unnest("address")

# Build a struct from multiple columns
pl.struct("city", "country").alias("location")
pl.struct(pl.col("lat"), pl.col("lon")).alias("coords")

# Modify a field inside the struct
pl.col("address").struct.with_fields(
    pl.field("city").str.to_uppercase()
)

```

### Decode JSON with an explicit dtype

The output dtype is required on the tested versions (`dtype` became
mandatory in 1.33). Match the schema to the actual payload:

```python
payload_dtype = pl.Struct({"a": pl.Int64, "b": pl.Boolean})
lf.select(pl.col("payload").str.json_decode(payload_dtype)).collect()
```

---

## Expression expansion & selectors — `polars.selectors`

One expression can expand to many columns, resolved against the schema when
the query runs. Prefer this over building one expression per column in a
Python loop or comprehension: expanded expressions run in parallel in one
context, and the code does not need to know the column list up front.

```python
pl.col("height", "weight")      # explicit names
pl.col(pl.Float64)              # by dtype
pl.col("^sales_.*$")            # regex — the ^...$ anchors are required
pl.all()                        # every column (same as pl.col("*"))
pl.all().exclude("id")          # everything but
```

Selectors are the richer form: they refer to groups of columns by type or
name pattern, and compose with set operations.

```python
import polars.selectors as cs

# By dtype family
cs.numeric()        # Int*, UInt*, Float*
cs.integer()        # Int*, UInt*
cs.float()          # Float32, Float64
cs.string()         # Utf8 / String
cs.boolean()
cs.temporal()       # Date, Datetime, Duration, Time
cs.categorical()
cs.binary()         # raw bytes columns

# By dtype instance
cs.by_dtype(pl.Int64, pl.Float64)

# By name pattern
cs.starts_with("sales_")
cs.ends_with("_id")
cs.contains("revenue")
cs.matches(r"^q\d$")    # regex; anchors are optional

# Set operations
cs.numeric() | cs.temporal()               # union
cs.numeric() - cs.ends_with("_id")         # difference
~cs.numeric()                              # complement (all non-numeric)
cs.numeric() & cs.starts_with("net_")     # intersection

# Use anywhere a column selector is accepted
lf.select(cs.numeric())
lf.select((cs.float() * 1.21).name.suffix("_with_tax"))
lf.with_columns(cs.string().str.to_uppercase())
lf.drop(cs.temporal())
lf.select(pl.all().exclude(cs.categorical()))
```

Caveats:

- One `col()` call cannot mix names/regexes with dtypes — use a selector, or
  two calls.
- An expanded expression keeps each source name, so two expansions over
  overlapping columns raise `DuplicateError`. Rename the whole set with
  `.name.suffix()` / `.name.prefix()` / `.name.map(fn)`; `.alias()` names a
  single output only.
- A selector's operators are overloaded for set algebra (`|`, `-`, `&`, `~`).
  Call `.as_expr()` first when you want the expression meaning instead —
  e.g. `(~cs.boolean().as_expr())` to negate boolean *values* rather than
  take the complement of the selected columns.
- **Polars 2.0:** combining a selector with `pl.col(...)` via `&`, `|`, or
  `^` no longer performs a column-set operation; it becomes an element-wise
  operator and can silently compute columns instead of selecting them. Use a
  selector on both sides, e.g.
  `cs.integer() & cs.by_name("id")`.
- Expansion that matches nothing produces no columns rather than an error, so
  a typo'd pattern shows up as a missing (or empty) result, not an exception.

---

## Casting

```python
# Strict (default) — raises on values that cannot be cast
pl.col("price").cast(pl.Float64)   # also pl.Int32, pl.Boolean, pl.Categorical

# Non-strict — converts unparseable values to null instead of raising
pl.col("price").cast(pl.Float64, strict=False)

# Common patterns
pl.col("ts_ms").cast(pl.Datetime("ms"))    # epoch ms to Datetime
pl.col("date_int").cast(pl.Date)           # epoch days to Date

# Inspect the dtype before casting
lf.collect_schema()["col_name"]
```

On Polars 2.0, casting a String column directly to `Date`, `Datetime`, or
`Time` is removed. Parse with `str.to_date()` / `str.to_datetime()` /
`str.to_time()`, then cast only the resulting temporal type if needed.
Integer ↔ categorical casts were also removed; use `.cat.to(dtype)` and
`.cat.physical()`. See `version-2.md`.

---

## when / then / otherwise

Strings inside `then()`/`otherwise()` are **column names**; wrap literal
values in `pl.lit()`.

```python
# Basic conditional
pl.when(pl.col("age") >= 18).then(pl.lit("adult")).otherwise(
    pl.lit("minor")
).alias("age_group")

# Multiple conditions (CASE WHEN)
pl.when(pl.col("score") >= 90).then(pl.lit("A")).when(
    pl.col("score") >= 80
).then(pl.lit("B")).when(pl.col("score") >= 70).then(pl.lit("C")).otherwise(
    pl.lit("F")
).alias("grade")

# Null handling
pl.col("v").fill_null(0)
pl.col("v").fill_null(strategy="forward")     # also: "backward"

# SUM(CASE WHEN ...) inside agg() — then() can take another column
lf.group_by("dept").agg(
    pl.when(pl.col("status") == "active")
    .then(pl.col("salary"))
    .otherwise(pl.lit(0))
    .sum()
    .alias("active_payroll")
)
```
