"""Reference solution for the deterministic eval tasks.

Run:
    python evals/run_eval.py --solution evals/reference_solution.py
"""

import polars as pl


def top_n_per_group(lf):
    return (
        lf.group_by("region", "product")
        .agg(pl.col("revenue").sum().alias("total"))
        .sort(
            "region",
            "total",
            "product",
            descending=[False, True, False],
            nulls_last=True,
        )
        .group_by("region", maintain_order=True)
        .head(3)
        .collect()
    )


def calendar_growth(lf):
    monthly = lf.group_by(
        pl.col("date").dt.truncate("1mo").alias("month")
    ).agg(pl.col("revenue").sum().alias("revenue"))
    previous = monthly.select(
        pl.col("month").dt.offset_by("1mo"),
        pl.col("revenue").alias("prev"),
    )
    return (
        monthly.join(previous, on="month", how="left", validate="1:1")
        .with_columns(
            growth_pct=pl.when(
                pl.col("prev").is_not_null() & (pl.col("prev") != 0)
            )
            .then((pl.col("revenue") / pl.col("prev") - 1) * 100)
            .otherwise(None)
        )
        .sort("month")
        .collect()
    )


def join_customers(orders, customers):
    return (
        orders.join(
            customers, on="customer_id", how="left", validate="m:1"
        )
        .select("order_id", "customer_id", "name", "amount")
        .sort("order_id")
        .collect()
    )


def tag_counts(lf):
    return (
        lf.explode("tags", empty_as_null=False)
        .drop_nulls("tags")
        .group_by("tags")
        .agg(pl.len().alias("n"))
        .sort("tags")
        .rename({"tags": "tag"})
        .collect()
    )


def scale_numeric(lf):
    return lf.with_columns(pl.col("a", "b") * 10).collect()
