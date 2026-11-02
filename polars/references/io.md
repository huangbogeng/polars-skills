# IO: Formats, Scans, Sinks, Lakehouse

Load this file when choosing a file format, reading/writing data, or wiring
Parquet, Delta, Iceberg, cloud, Excel, or database sources.

## Contents

Format matrix; scan vs read; sink vs write; Parquet; encrypted Parquet;
Delta; Iceberg; cloud storage; Excel, databases, and Arrow; anti-patterns.

## Format matrix

| Format | Read | Scan | Write / sink | Extra |
|---|---|---|---|---|
| CSV | `read_csv` | `scan_csv` | `write_csv` / `sink_csv` | built-in |
| Parquet | `read_parquet` | `scan_parquet` | `write_parquet` / `sink_parquet` | built-in |
| NDJSON | `read_ndjson` | `scan_ndjson` | `write_ndjson` / `sink_ndjson` | built-in |
| IPC / Arrow | `read_ipc` | `scan_ipc` | `write_ipc` | built-in |
| Avro | `read_avro` | — | — | built-in |
| Delta Lake | `read_delta` | `scan_delta` | — | `polars[deltalake]` |
| Iceberg | — | `scan_iceberg` | `write_iceberg` / `sink_iceberg` | `polars[iceberg]` |
| Excel | `read_excel` | — | `DataFrame.write_excel` | `polars[excel]` |
| Database | `read_database_uri` | — | — | `polars[database]` |
| PyArrow dataset | — | `scan_pyarrow_dataset` | — | `pyarrow` |

Prefer Parquet for repeated scans; prefer `scan_*` over `read_*` for
file-backed pipelines.

## Scan vs read

`read_*` materializes a `DataFrame`; `scan_*` returns a `LazyFrame` and lets
the optimizer push filters and projections into the reader.

```python
lf = pl.scan_parquet("events/*.parquet")
result = (
    lf.filter(pl.col("event") == "purchase")
    .select("user_id", "ts", "amount")
    .collect()
)
```

Use `read_*` when the caller needs an eager frame now, or an API only accepts
`DataFrame`. Otherwise keep the scan in the query chain.

## Sink vs write

`write_*` materializes the frame and writes it. `sink_*` streams the query to
storage and does not require holding the result in memory.

```python
lf.sink_parquet("out.parquet")
lf.sink_csv("out.csv")
lf.sink_ndjson("out.ndjson")
```

Use `sink_*` for large outputs or when a regular `collect()` is killed by the
OS. For multiple outputs from one query, use lazy sinks plus `collect_all`;
see `performance.md`.

## Parquet

Parquet is columnar, compressed, and carries statistics for row-group
skipping. Tune write-side options only with a benchmark:

```python
lf.sink_parquet(
    "out.parquet",
    compression="zstd",
    compression_level=3,
    row_group_size=250_000,
    statistics=True,
)
```

- `partition_by=` / `pl.PartitionBy` writes directories that later scans can
  prune with a filter.
- `scan_parquet(..., use_statistics=True)` is the default.
- `hive_partitioning` reads `key=value` directory names as columns.
- `include_file_paths` adds the source path for provenance.

## Encrypted Parquet

2.1 reads and writes Parquet modular encryption. Pass property objects to
`read_parquet(decryption=...)` and `write_parquet(encryption=...)`.

```python
import tempfile
from pathlib import Path

key = b"0123456789012345"
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "secure.parquet"
    frame = pl.DataFrame({"id": [1, 2], "secret": ["a", "b"]})
    frame.write_parquet(
        path,
        encryption=pl.ParquetEncryptionProperties(footer_key=key),
    )
    result = pl.read_parquet(
        path,
        decryption=pl.ParquetDecryptionProperties(footer_key=key),
    )
    assert result.equals(frame)
```

`column_keys={"name": key}` encrypts only selected columns. With any column
keys, columns without a key are written unencrypted. `plaintext_footer=True`
lets readers without the footer key see the schema and unencrypted columns;
the footer is still signed. Encryption is unstable in 2.1; keep keys out of
source control and serialized plans.

## Delta

Delta Lake is read lazily with `scan_delta`; `read_delta` is the eager form.
Both accept a path, a `DeltaTable`, and `version=` for time travel.

```python
from datetime import date

pl.scan_delta("lake/orders", version=3).filter(
    pl.col("order_date") >= date(2026, 1, 1)
).collect()
```

Requires `polars[deltalake]`. Delta column mapping is not supported; Polars
raises rather than guessing. Prefer Parquet when you do not need Delta
transactions or time travel.

## Iceberg

Use `scan_iceberg` for reads and `sink_iceberg` / `write_iceberg` for writes;
SQL catalogs and DDL are covered in `iceberg.md`.

```python
pl.scan_iceberg("file:/warehouse/sales/metadata/v3.metadata.json")
```

Requires `polars[iceberg]`. Prefer the native reader; metadata statistics can
skip files.

## Cloud storage

`scan_*` / `read_*` accept cloud URLs and `storage_options` for credentials.
Filters and projections still push down, so keep them in the lazy chain.

```python
pl.scan_parquet(
    "s3://bucket/events/*.parquet",
    storage_options={
        "s3.region": "eu-central-1",
        "s3.access-key-id": "ACCESS_KEY",
        "s3.secret-access-key": "SECRET_KEY",
    },
)
```

For credentials from the environment, leave `credential_provider="auto"`.
Do not embed secrets in serialized query plans.

## Excel, databases, and Arrow

These are convenience entries, not the main file pipeline:

```python
pl.read_excel("book.xlsx", sheet_name="Sheet1")
pl.read_database_uri(
    "SELECT id, amount FROM orders WHERE amount > 0",
    uri="postgresql://user:pass@host/db",
)
pl.scan_pyarrow_dataset(dataset)
```

- Excel: `read_excel` uses calamine by default; `DataFrame.write_excel`
  needs `polars[excel]`.
- Database: `read_database_uri` supports `partition_on` / `partition_num`;
  requires the `polars[database]` extra.
- PyArrow: `scan_pyarrow_dataset` keeps a dataset lazy; requires `pyarrow`.
- Avro: read-only via `read_avro`; there is no scan form.

## Anti-patterns

- `read_*` followed by a long chain for a file pipeline; use `scan_*`.
- `collect()` before a `sink_*`; sink the lazy frame directly.
- CSV for repeated scans; convert to Parquet.
- Loading a whole database table when a filtered query or partition read
  suffices.
- Embedding credentials in code or serialized plans.
- Assuming every format supports scan; Avro and Excel do not.

## References

- Parquet: https://docs.pola.rs/user-guide/io/parquet/
- Cloud storage: https://docs.pola.rs/user-guide/io/cloud-storage/
- Sources and sinks: https://docs.pola.rs/user-guide/lazy/sources_sinks/
- `scan_delta`: https://docs.pola.rs/api/python/stable/reference/api/polars.scan_delta.html
- `read_excel`: https://docs.pola.rs/api/python/stable/reference/api/polars.read_excel.html
- `read_database_uri`: https://docs.pola.rs/api/python/stable/reference/api/polars.read_database_uri.html
- `scan_pyarrow_dataset`: https://docs.pola.rs/api/python/stable/reference/api/polars.scan_pyarrow_dataset.html
