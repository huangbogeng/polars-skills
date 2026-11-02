# Iceberg

Load this file when the task reads or writes Apache Iceberg tables, attaches
an Iceberg catalog to SQL, or deletes Iceberg rows. Single-node Polars only;
Polars Cloud and distributed execution are out of scope. The 2.1 Iceberg APIs
are **unstable** and need the optional extra:

```bash
pip install "polars[iceberg]"
```

Examples need a live catalog or table, so they are not run by the example
suite; the API shapes follow the 2.1 reference.

## Contents

Scanning tables; writing and sinking; deleting rows; SQL catalogs and DDL;
transaction and schema caveats; API links.

## Scanning

`scan_iceberg` accepts a PyIceberg `Table`, a `namespace.table` identifier,
or an absolute path to a metadata file. It returns a `LazyFrame`.

```python
table_path = "file:/warehouse/sales/metadata/v3.metadata.json"
lf = pl.scan_iceberg(table_path)
result = lf.filter(pl.col("year") == 2024).collect()
```

For object stores, pass PyIceberg storage options:

```python
lf = pl.scan_iceberg(
    "s3://bucket/warehouse/sales/metadata/v3.metadata.json",
    storage_options={
        "s3.region": "eu-central-1",
        "s3.access-key-id": "ACCESS_KEY",
        "s3.secret-access-key": "SECRET_KEY",
    },
)
```

Use `catalog=` when the source is a table identifier rather than a metadata
path. Snapshot options select a point in time or an incremental append scan:

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog("lake")

pl.scan_iceberg(
    "sales.orders",
    catalog=catalog,
    snapshot_id=7051579356916758811,
)

pl.scan_iceberg(
    "sales.orders",
    catalog=catalog,
    from_snapshot_id_exclusive=7051579356916758811,
    to_snapshot_id_inclusive=8051579356916758811,
)
```

`use_metadata_statistics=True` (default) lets filters skip data files using
Iceberg min/max statistics and can answer `select(pl.len())` from metadata.
`fast_deletion_count=True` extends that to tables with position deletes, but
can be wrong when position delete files contain duplicate entries.
`reader_override="native" | "pyiceberg"` is for testing; leave it unset.

Metadata caching: manifests and metadata files whose names carry a UUID are
cached for the process lifetime, keyed by storage and catalog properties.
The cache assumes environment credentials do not change.

## Writing and sinking

`LazyFrame.sink_iceberg` is the streaming writer and returns a `DataFrame`
containing the new metadata path:

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog("lake")

metadata = (
    pl.scan_parquet("staging/orders/*.parquet")
    .sink_iceberg(
        "sales.orders",
        catalog=catalog,
        mode="append",                     # or "overwrite"
        create_if_not_exists=True,
        create_namespace_if_not_exists=True,
    )
)
```

For an eager frame, `DataFrame.write_iceberg(target, mode)` is the short
form; `mode` is required and accepts `"append"` or `"overwrite"`.

Overwrite controls:

```python
lf.sink_iceberg(
    "sales.orders",
    catalog=catalog,
    mode="overwrite",
    overwrite_filter=pl.col("order_date") == date(2026, 10, 1),
    partition_overwrite="dynamic",
    schema_mode="merge",                   # or "overwrite"
)
```

Here `lf` and `catalog` are the frame and catalog from the preceding
examples; `date` is `datetime.date`.

- `overwrite_filter` deletes only matching existing rows; incoming rows are
  not checked against it. A Polars expression must convert to an Iceberg
  expression with the same semantics, so only comparisons with literals,
  `is_in`, `is_between`, `is_null`, `is_not_null`, `is_nan`, `is_not_nan`,
  and `str.starts_with` combined with `&`, `|`, and `~` are supported. A
  PyIceberg `BooleanExpression` or filter string is passed through.
- `partition_overwrite="dynamic"` deletes only the partitions receiving new
  rows. It requires identity transforms on non-floating, non-nanosecond,
  non-nullable-struct source columns.
- `schema_mode="merge"` evolves the table with new fields and compatible
  promotions; `"overwrite"` replaces the schema and requires
  `mode="overwrite"`. Schema overwrite is not supported for partitioned
  tables or tables with a sort order.
- `create_if_not_exists=True` derives an unpartitioned, unsorted table from
  the frame schema and creates it before the query runs, so the empty table
  remains if the query fails. `create_namespace_if_not_exists=True` requires
  it. Small/unsigned integers widen losslessly; `UInt64`, nanosecond
  `Datetime`, and `Duration` raise before creation.
- `maintain_order=False` can improve write throughput. Sorting applies the
  table sort order to incoming rows and materializes the incoming data in
  memory; existing files are not rewritten.

Partitioned writes support identity, year, month, day, hour, truncate, and
bucket transforms, including fields nested in structs. Void and decimal
truncation are not supported.

## Deleting rows

`pl.delete_iceberg` removes rows matching a predicate:

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog("lake")
table = catalog.load_table("sales.orders")
pl.delete_iceberg(table, pl.col("id").is_in([1, 2, 3]))
```

Rows for which the predicate is null are kept, matching `DataFrame.remove`.
The target is a PyIceberg table or a table name. This is a metadata commit;
data files that only partially match are rewritten by PyIceberg.

## SQL catalogs and DDL

`SQLContext.register_catalog` attaches a PyIceberg catalog so SQL can query
and manage its tables by name:

```python
from pyiceberg.catalog import load_catalog

ctx = pl.SQLContext()
ctx.register_catalog(
    "lake",
    load_catalog("lake"),
    default_namespace="sales",
    default=True,
)

ctx.execute("SELECT * FROM sales.orders").collect()
ctx.execute("SELECT * FROM lake.sales.orders").collect()
```

`default=True` makes the catalog handle names without an attached catalog
prefix; `default_namespace` supplies the namespace for bare table names.
`unregister_catalog(name)` detaches it. Register again to replace one; a
name differing only in case is an error.

Name resolution order: a CTE or context table, then an attached catalog
prefix, then the default catalog. Catalog identifiers are case-sensitive.
Columns may be qualified by any trailing part of the full table name.

For clients without a catalog server, construct a catalog config directly:

```python
from pyiceberg.catalog.sql import SqlCatalog
from polars.io.iceberg import IcebergCatalogConfig

catalog = IcebergCatalogConfig(
    class_=SqlCatalog,
    name="lake",
    properties={
        "uri": "sqlite:////tmp/iceberg.db",
        "warehouse": "file:///tmp/warehouse",
    },
)
ctx.register_catalog("lake", catalog, default=True)
```

SQL DDL on attached catalogs (2.1):

```python
ctx.execute(
    "CREATE TABLE lake.sales.orders ("
    "id BIGINT NOT NULL, "
    "order_date DATE, "
    "payload STRUCT<sku VARCHAR, qty BIGINT>)"
)
ctx.execute("DROP TABLE lake.sales.orders PURGE")
```

- `CREATE TABLE <name> (<columns>)` resolves the name like a query. With a
  default catalog, `CREATE TABLE sales.t (...)` creates `sales.t` there;
  use `CREATE TEMP TABLE` for a context table instead.
- `NOT NULL` becomes a required field. `LOCATION '...'` and
  `WITH ('key' = 'value')` set the location and table properties.
- `DROP TABLE [IF EXISTS] <name> [PURGE]` drops a context table first, then a
  catalog table. A catalog drop is permanent at `execute()`; `PURGE` also
  asks the catalog to delete data files and applies only to catalog tables.
- `CREATE TABLE ... AS SELECT`, `TRUNCATE`, and `DELETE` on catalog tables
  are not supported yet. `TRUNCATE TABLE IF EXISTS` of a name without an
  attached catalog prefix acts only on a context table.
- Catalogs attached inside a `with SQLContext()` block detach on exit.

## Caveats

- **Unstable API.** Catalog registration, Iceberg writes and deletes, and
  catalog DDL can change in a later release.
- **Serialized plans may contain credentials.** Catalog properties and
  storage options are embedded when a plan is serialized; treat such plans
  as secrets.
- **`SQLContext` is not thread-safe.** Do not register tables or catalogs
  while another thread executes on the same context.
- **Table loading is lazy.** A missing catalog table can raise as late as
  `collect()`, not at `execute()`.
- **Required fields.** `DataFrame.write_iceberg` rejects nulls in required
  columns; required fields nested in structs/lists are not supported.

## API links

- `polars.scan_iceberg`
- `polars.LazyFrame.sink_iceberg`
- `polars.DataFrame.write_iceberg`
- `polars.delete_iceberg`
- `polars.SQLContext.register_catalog`
