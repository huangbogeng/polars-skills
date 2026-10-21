"""Deterministic tasks for evaluating the Polars skill.

Each task exposes the fixtures and an exact expected result. The runner is
intentionally independent of any model: an agent writes a solution module,
then `run_eval.py` grades it locally. This measures task success, not model
calls or trigger rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import polars as pl
from polars.testing import assert_frame_equal


def _collect(frame):
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame


def top_n_fixture():
    return {
        "orders": pl.LazyFrame(
            {
                "region": ["r"] * 5 + ["s"] * 2,
                "product": ["e", "d", "c", "b", "a", "y", "x"],
                "revenue": [7, 8, 9, 10, 10, 1, 2],
            }
        )
    }


def top_n_expected():
    return pl.DataFrame(
        {
            "region": ["r", "r", "r", "s", "s"],
            "product": ["a", "b", "c", "x", "y"],
            "total": [10, 10, 9, 2, 1],
        }
    )


def top_n_check(module):
    got = _collect(module.top_n_per_group(top_n_fixture()["orders"]))
    assert_frame_equal(got, top_n_expected())


def growth_fixture():
    return {
        "monthly": pl.LazyFrame(
            {
                "date": [
                    date(2023, 12, 31),
                    date(2024, 1, 31),
                    date(2024, 3, 31),
                    date(2024, 4, 30),
                    date(2024, 5, 31),
                    date(2024, 6, 30),
                ],
                "revenue": [50, 100, 200, 0, 50, 75],
            }
        )
    }


def growth_expected():
    return pl.DataFrame(
        {
            "month": [
                date(2023, 12, 1),
                date(2024, 1, 1),
                date(2024, 3, 1),
                date(2024, 4, 1),
                date(2024, 5, 1),
                date(2024, 6, 1),
            ],
            "revenue": [50, 100, 200, 0, 50, 75],
            "prev": [None, 50, None, 200, 0, 50],
            "growth_pct": [None, 100.0, None, -100.0, None, 50.0],
        },
        schema={
            "month": pl.Date,
            "revenue": pl.Int64,
            "prev": pl.Int64,
            "growth_pct": pl.Float64,
        },
    )


def growth_check(module):
    got = _collect(module.calendar_growth(growth_fixture()["monthly"]))
    assert_frame_equal(got, growth_expected())


def join_fixture():
    return {
        "orders": pl.LazyFrame(
            {"order_id": [1, 2, 3], "customer_id": [10, 20, 99], "amount": [100, 50, 25]}
        ),
        "customers": pl.LazyFrame({"customer_id": [10, 20], "name": ["alice", "bob"]}),
    }


def join_expected():
    return pl.DataFrame(
        {
            "order_id": [1, 2, 3],
            "customer_id": [10, 20, 99],
            "name": ["alice", "bob", None],
            "amount": [100, 50, 25],
        }
    )


def join_check(module):
    fixtures = join_fixture()
    got = _collect(module.join_customers(fixtures["orders"], fixtures["customers"]))
    assert_frame_equal(got, join_expected())

    duplicate = pl.LazyFrame({"customer_id": [10, 10], "name": ["a", "b"]})
    try:
        _collect(module.join_customers(fixtures["orders"], duplicate))
    except Exception:
        # The contract is "must raise", not a particular exception type.
        # `validate="m:1"` raises ComputeError; a hand-written uniqueness
        # check usually raises ValueError. Fan-out (returning rows) fails.
        pass
    else:
        raise AssertionError("duplicate dimension keys must raise")


def tags_fixture():
    return {
        "events": pl.LazyFrame(
            {"id": [1, 2, 3, 4], "tags": [["a", "b"], [], None, ["a"]]}
        )
    }


def tags_expected():
    return pl.DataFrame({"tag": ["a", "b"], "n": [2, 1]})


def tags_check(module):
    got = _collect(module.tag_counts(tags_fixture()["events"]))
    # The count dtype is an implementation detail; values and order are not.
    assert_frame_equal(got, tags_expected(), check_dtypes=False)


def scale_fixture():
    return {
        "metrics": pl.LazyFrame(
            {"id": [1, 2], "a": [1, 2], "b": [3, 4], "label": ["x", "y"]}
        )
    }


def scale_expected():
    return pl.DataFrame(
        {"id": [1, 2], "a": [10, 20], "b": [30, 40], "label": ["x", "y"]}
    )


def scale_check(module):
    got = _collect(module.scale_numeric(scale_fixture()["metrics"]))
    assert_frame_equal(got, scale_expected())


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    contract: str
    fixtures: Callable[[], dict[str, pl.DataFrame | pl.LazyFrame]]
    check: Callable[[object], None]


TASKS = [
    Task(
        id="top_n_per_group",
        prompt=(
            "For each region, return the top 3 products by total revenue. "
            "Break revenue ties by product name ascending, and order regions "
            "ascending. The result must be deterministic."
        ),
        contract=(
            "Define top_n_per_group(lf) -> DataFrame with columns "
            "region, product, total, ordered by region asc, total desc, "
            "product asc."
        ),
        fixtures=top_n_fixture,
        check=top_n_check,
    ),
    Task(
        id="calendar_growth",
        prompt=(
            "Compute month-over-month revenue growth against the previous "
            "calendar month. Missing months are not zero. Leave growth null "
            "when the previous value is missing or zero."
        ),
        contract=(
            "Define calendar_growth(lf) -> DataFrame with columns month "
            "(Date, first of month), revenue, prev, growth_pct, ordered by month."
        ),
        fixtures=growth_fixture,
        check=growth_check,
    ),
    Task(
        id="validate_join",
        prompt=(
            "Attach customer names to orders with a left join. The customer "
            "table is a dimension table and must have unique customer_id; "
            "duplicate keys must raise rather than fan out."
        ),
        contract=(
            "Define join_customers(orders, customers) -> DataFrame with "
            "columns order_id, customer_id, name, amount, ordered by order_id."
        ),
        fixtures=join_fixture,
        check=join_check,
    ),
    Task(
        id="tag_counts",
        prompt=(
            "Count how often each tag appears. Empty and null tag lists must "
            "not create rows or null tags. Order by tag ascending."
        ),
        contract=(
            "Define tag_counts(lf) -> DataFrame with columns tag, n, "
            "ordered by tag ascending."
        ),
        fixtures=tags_fixture,
        check=tags_check,
    ),
    Task(
        id="scale_numeric",
        prompt=(
            "Multiply the numeric measure columns a and b by 10 while keeping "
            "id and label unchanged. Preserve the original column order."
        ),
        contract=(
            "Define scale_numeric(lf) -> DataFrame with columns "
            "id, a, b, label in that order."
        ),
        fixtures=scale_fixture,
        check=scale_check,
    ),
]


def find(task_id: str) -> Task:
    for task in TASKS:
        if task.id == task_id:
            return task
    raise KeyError(f"unknown task {task_id!r}")
