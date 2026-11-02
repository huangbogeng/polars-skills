# Nested and Categorical Types

Load this file for `Array`, `Map`, `Categorical`, `Enum`, or `Binary`
columns. `List` and `Struct` basics are in `expressions.md`.

## Contents

`Array` vs `List`; `Array` operations; `Map`; `Categorical` vs `Enum`;
`Categories`; `Binary`; choosing a type.

## `Array` vs `List`

`List` is variable-length; `Array` is a fixed shape. Prefer `Array` when every
row has the same shape: it is more memory efficient and faster.

```python
array = pl.Series("a", [[1, 2], [3, 4]], dtype=pl.Array(pl.Int64, 2))
assert array.dtype == pl.Array(pl.Int64, 2)
assert array.arr.get(1).to_list() == [2, 4]
assert array.arr.sum().to_list() == [3, 7]
assert array.arr.to_list().to_list() == [[1, 2], [3, 4]]
```

Polars infers `List` by default. Pass `dtype=pl.Array(...)` or use
`list.to_array(width)` when the width is known. `arr.*` covers element access,
slicing, aggregation, `eval`, `to_list`, and `to_struct`.

## `Map`

A `Map` is a key-value container per row. Construct it with an explicit
schema; `map.*` reads keys/values and looks up a key.

```python
mapped = pl.DataFrame(
    {"m": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]},
    schema={"m": pl.Map(pl.String, pl.Int64)},
)
result = mapped.select(
    pl.col("m").map.get("a").alias("a"),
    pl.col("m").map.keys().alias("keys"),
    pl.col("m").map.len().alias("n"),
)
assert result["a"].to_list() == [1, 3]
assert result["keys"].to_list() == [["a", "b"], ["a", "b"]]
assert result["n"].to_list() == [2, 2]
```

`map.*` also has `contains_key`, `entries`, and `values`. `Map` is mainly an
IO/SQL type; for computation, prefer separate columns or `Struct` unless the
data is genuinely key-value.

## `Categorical` vs `Enum`

| | `Enum` | `Categorical` |
|---|---|---|
| Categories | fixed, declared up front | discovered dynamically |
| Ordering | ordered by declaration | lexical comparison with strings |
| Encoding | fixed mapping; shareable | global mapping with lookups |
| Performance | preferred | flexible, more bookkeeping |

Use `Enum` whenever the category set is known; use `Categorical` only when
categories grow with the data.

```python
categorical = pl.Series("c", ["b", "a", "b"], dtype=pl.Categorical)
enum = categorical.cast(pl.Enum(["a", "b"]))
assert enum.to_list() == ["b", "a", "b"]
assert enum.cat.physical().to_list() == [1, 0, 1]
assert categorical.cat.physical().dtype == pl.UInt32
assert (enum > "a").to_list() == [True, False, True]
```

- `cat.physical()` returns the integer encoding.
- `.cast(pl.Enum([...]))` converts `Categorical` to `Enum`; `.cast(pl.Categorical)`
  goes the other way.
- `.cat.to(dtype)` converts from the physical integer representation into a
  categorical/enum dtype; it is not a string conversion.
- `cat.to(pl.String)` is invalid; use `.cast(pl.String)`.
- Comparing against an unknown `Enum` value raises.

## `Categories`

By default all `Categorical` columns share a global mapping, so independent
columns can be concatenated without re-encoding. Use `pl.Categories` for
independent category spaces and a smaller physical type.

```python
species = pl.Categories("species", physical=pl.UInt8)
coded = pl.Series(
    ["bear", "wolf", "bear"], dtype=pl.Categorical(species)
)
assert coded.cat.physical().dtype == pl.UInt8
```

`physical` can be `UInt8`, `UInt16`, or `UInt32` (default). `StringCache` and
`enable_string_cache()` are deprecated and do nothing in current Polars.

## `Binary`

`Binary` stores raw bytes. Use `.bin.*` for byte operations and
`.cast(pl.String)` for UTF-8 text.

```python
raw = pl.Series("b", [b"abc", b"abd"], dtype=pl.Binary)
assert raw.bin.contains(b"bc").to_list() == [True, False]
assert raw.bin.size().to_list() == [3, 3]
assert raw.bin.encode("hex").to_list() == ["616263", "616264"]
assert raw.cast(pl.String).to_list() == ["abc", "abd"]
```

`bin.*` also covers `starts_with`, `ends_with`, `slice`, `head`, `tail`,
`get`, and `reinterpret`. `bin.decode` is for hex/base64, not UTF-8.

## Choosing a type

- Fixed-width numeric/string vectors: `Array` over `List`.
- Named fields: `Struct`; key-value pairs: `Map`.
- Low-cardinality strings with known values: `Enum`; unknown/dynamic:
  `Categorical` with optional `Categories`.
- Raw bytes: `Binary`; text: `String`.
- Avoid `Object` for computation; it limits native expressions.

## References

- Lists and arrays: https://docs.pola.rs/user-guide/expressions/lists-and-arrays/
- Categoricals and enums: https://docs.pola.rs/user-guide/expressions/categorical-data-and-enums/
- `Array`: https://docs.pola.rs/api/python/stable/reference/api/polars.datatypes.Array.html
- `Map`: https://docs.pola.rs/api/python/stable/reference/api/polars.datatypes.Map.html
- `Categories`: https://docs.pola.rs/api/python/stable/reference/api/polars.datatypes.Categories.html
- `StringCache` deprecation: https://docs.pola.rs/api/python/stable/reference/api/polars.StringCache.html
