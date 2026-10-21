"""Execute documentation examples against small fixtures with known results."""

import ast
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
import os
import re
import tempfile
import unittest

import polars as pl
import polars.selectors as cs
from polars.testing import assert_frame_equal


SKILL = Path(__file__).resolve().parents[1] / "polars"
FILES = Path(__file__).resolve().parents[1] / "evals" / "files"
MAJOR = int(pl.__version__.split(".")[0])


def executable_blocks(reference, heading, block=0, *, lf=None):
    """Return the top-level expressions of a fenced block, in order.

    ``run_example`` only exposes names bound by the block (assignment,
    ``def``); ``executable_blocks`` exposes the statements whose value the
    docs rely on, so a block that is a bare chain can be asserted directly.
    ``lf`` supplies the context frame for blocks that only chain on it.
    """
    path = SKILL / "references" / reference
    code = section_blocks(reference, heading)[1][block]
    namespace = {"pl": pl, "cs": cs, "date": date, "datetime": datetime}
    if lf is not None:
        namespace["lf"] = lf
    expressions = []
    for statement in ast.parse(code, filename=str(path)).body:
        if not isinstance(statement, ast.Expr):
            raise AssertionError(
                f"{reference} {heading!r}: expected only expressions"
            )
        expressions.append(
            eval(compile(ast.Expression(statement.value), str(path), "eval"),
                 namespace)
        )
    return expressions


@contextmanager
def chdir(path):
    """Run file-writing examples in a temporary directory."""
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def source_blocks(path):
    """Return every fenced Python block in a Markdown file."""
    blocks = []
    code_lines = None
    for line in path.read_text().splitlines():
        if line == "```python":
            code_lines = []
        elif line == "```" and code_lines is not None:
            blocks.append("\n".join(code_lines))
            code_lines = None
        elif code_lines is not None:
            code_lines.append(line)
    return blocks


def section_blocks(reference, heading):
    """Return the Python blocks under a heading, up to the next heading."""
    path = SKILL / "references" / reference
    section = path.read_text().split(heading + "\n", 1)[1]
    blocks = []
    code_lines = None
    for line in section.splitlines():
        if line == "```python":
            code_lines = []
        elif line == "```" and code_lines is not None:
            blocks.append("\n".join(code_lines))
            code_lines = None
        elif code_lines is not None:
            code_lines.append(line)
        elif re.match(r"^#{1,6} ", line):
            break
    return path, blocks


def run_example(reference, heading, *, block=0, **inputs):
    """Execute a fenced example and return its namespace."""
    path, blocks = section_blocks(reference, heading)
    namespace = {"pl": pl, **inputs}
    exec(
        compile(blocks[block], str(path), "exec"),
        namespace,
    )
    return namespace


def example(reference, heading, *, block=0, expression=-1, **inputs):
    """Run a fenced example in its own namespace and return its last expression.

    Headings locate examples; assertions below check their actual output rather
    than wording. No query implementation is copied into the tests.
    """
    path, blocks = section_blocks(reference, heading)
    code = blocks[block]
    tree = ast.parse(code, filename=str(path))
    namespace = {"pl": pl, **inputs}
    results = []
    for statement in tree.body:
        if isinstance(statement, ast.Expr):
            result = eval(
                compile(ast.Expression(statement.value), str(path), "eval"),
                namespace,
            )
            results.append(result)
        else:
            exec(
                compile(ast.Module([statement], type_ignores=[]), str(path), "exec"),
                namespace,
            )
    return results[expression]


class DocumentationExamples(unittest.TestCase):
    def test_every_python_block_parses(self):
        paths = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
        for path in paths:
            for index, code in enumerate(source_blocks(path)):
                with self.subTest(path=path.name, block=index):
                    ast.parse(code, filename=str(path))


class InsightExamples(unittest.TestCase):
    def test_top_three_with_ties_and_multiple_regions(self):
        data = pl.DataFrame({
            "region": ["r"] * 5 + ["s", "s"],
            "product": ["e", "d", "c", "b", "a", "y", "x"],
            "revenue": [7, 8, 9, 10, 10, 1, 2],
        })
        heading = '## "Top N per group" (top 3 products per region)'
        result = example("insight-recipes.md", heading, lf=data.lazy())
        self.assertEqual(result.to_dicts(), [
            {"region": "r", "product": "a", "total": 10},
            {"region": "r", "product": "b", "total": 10},
            {"region": "r", "product": "c", "total": 9},
            {"region": "s", "product": "x", "total": 2},
            {"region": "s", "product": "y", "total": 1},
        ])
        assert_frame_equal(result, example(
            "insight-recipes.md", heading, lf=data.reverse().lazy(),
        ))

    def test_calendar_growth_missing_month_and_zero_denominator(self):
        for temporal in (date, datetime):
            with self.subTest(temporal=temporal):
                result = example(
                    "insight-recipes.md",
                    '## "How did X change vs. the previous period?"',
                    lf=pl.LazyFrame({
                        "date": [temporal(2023, 12, 31), temporal(2024, 1, 31),
                                 temporal(2024, 3, 31), temporal(2024, 4, 30),
                                 temporal(2024, 5, 31), temporal(2024, 6, 30)],
                        "revenue": [50, 100, 200, 0, 50, 75],
                    }),
                )
                self.assertEqual(result["prev"].to_list(),
                                 [None, 50, None, 200, 0, 50])
                self.assertEqual(result["growth_pct"].to_list(),
                                 [None, 100.0, None, -100.0, None, 50.0])
                self.assertEqual(result.height, 6)

    def test_all_null_sum_depends_on_column_dtype(self):
        heading = '## "How did X change vs. the previous period?"'
        typed = example(
            "insight-recipes.md", heading,
            lf=pl.LazyFrame({
                "date": pl.Series([date(2024, 1, 31), date(2024, 2, 29)]),
                "revenue": pl.Series([None, 100], dtype=pl.Int64),
            }),
        )
        self.assertEqual(typed["revenue"].to_list(), [0, 100])
        self.assertEqual(typed["growth_pct"].to_list(), [None, None])
        all_null = pl.LazyFrame({"revenue": [None, None]})
        self.assertEqual(all_null.collect_schema()["revenue"], pl.Null)
        self.assertEqual(
            all_null.select(pl.col("revenue").sum()).collect().item(), None
        )

    def test_share_of_total(self):
        result = example(
            "insight-recipes.md", '## "What share does each X have of the total?"',
            lf=pl.LazyFrame({"category": ["a", "b"], "revenue": [75, 25]}),
        )
        self.assertEqual(result["share_pct"].to_list(), [75.0, 25.0])

    def test_zero_total_share_is_undefined(self):
        result = example(
            "insight-recipes.md", '## "What share does each X have of the total?"',
            lf=pl.LazyFrame({"category": ["a", "b"], "revenue": [100, -100]}),
        )
        self.assertEqual(result["share_pct"].to_list(), [None, None])

    def test_quantile_bins_tolerate_ties(self):
        for values in ([1, 1, 1, 1, 2], [1] * 5):
            with self.subTest(values=values):
                result = example(
                    "insight-recipes.md", '## "What does the distribution of X look like?"',
                    block=2 if hasattr(pl.Expr, "bin_quantiles") else 1,
                    lf=pl.LazyFrame({"segment": ["a"] * 5, "order_value": values}),
                )
                self.assertEqual(result["n"].sum(), 5)
                self.assertLessEqual(result.height, 4)
                self.assertEqual(result["bucket"].null_count(), 0)

    def test_rolling_forms_agree_with_nulls(self):
        heading = '## "What is the trailing N-day value?" (sliding windows)'
        inputs = {"lf": pl.LazyFrame({
            "ts": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 10)],
            "revenue": [1.0, None, 3.0],
        })}
        grouped = example("insight-recipes.md", heading, expression=0, **inputs)
        annotated = example("insight-recipes.md", heading, expression=1, **inputs)
        self.assertEqual(grouped["rev_7d"].to_list(), [1.0, 1.0, 3.0])
        assert_frame_equal(grouped, annotated.select("ts", "rev_7d"))


class JoinAndReshapeExamples(unittest.TestCase):
    def test_join_cardinality_rejects_duplicate_lookup_keys(self):
        with self.assertRaises(pl.exceptions.ComputeError):
            example(
                "joins.md", "## validate= — assert key cardinality",
                lf=pl.LazyFrame({"id": [1, 2]}),
                dim=pl.LazyFrame({"id": [1, 1]}),
                other=pl.LazyFrame({"id": [1, 1]}),
            ).collect()

    def test_asof_does_not_match_future_quotes(self):
        result = example(
            "joins.md", "## join_asof — nearest match on an ordered key",
            trades=pl.LazyFrame({"ts": [1, 3, 5]}),
            quotes=pl.LazyFrame({"ts": [2, 4], "price": [20, 40]}),
        ).collect()
        self.assertEqual(result["price"].to_list(), [None, 20, 40])

    def test_range_join_is_half_open(self):
        result = example(
            "joins.md", "## join_where — inequality / non-equi / range joins",
            events=pl.LazyFrame({"ts": [1, 2, 3]}),
            windows=pl.LazyFrame({"start": [1], "end": [3]}),
        ).collect().sort("ts")
        self.assertEqual(result["ts"].to_list(), [1, 2])

    def test_lazy_pivot_column_order(self):
        result = example(
            "reshaping.md", "## pivot() — long → wide", block=1,
            lf=pl.LazyFrame({
                "ix": ["a", "a"], "col": ["x", "y"], "val": [1, 2],
            }),
        )
        self.assertEqual(result.columns, ["ix", "y", "x"])
        self.assertEqual(result.to_dicts(), [{"ix": "a", "y": 2, "x": 1}])

    def test_crosstab_empty_cells_are_zero(self):
        data = pl.DataFrame({
            "age_group": ["young", "old"], "activity_quartile": ["q1", "q2"],
        })
        result = example(
            "pandas-to-polars.md",
            '## `pd.crosstab` → `.pivot()` with `aggregate_function="len"`',
            block=1, df=data,
        )
        self.assertEqual(result.select("q1", "q2").rows(), [(1, 0), (0, 1)])


@unittest.skipUnless(
    pl.__version__.split(".")[0] == "2", "Polars 2.0 behavior"
)
class Version2Examples(unittest.TestCase):
    def test_join_order_is_explicit(self):
        scope = run_example(
            "version-2.md", "## Execution order is no longer incidental"
        )
        self.assertEqual(scope["joined"]["label"].to_list(), ["a", "b", "c"])
        self.assertEqual(scope["joined"]["value"].to_list(), ["z", "y", "x"])

    def test_horizontal_concat_is_strict(self):
        with self.assertRaises(pl.exceptions.ShapeError):
            pl.concat(
                [pl.DataFrame({"a": [1, 2, 3]}), pl.DataFrame({"b": [4, 5]})],
                how="horizontal",
            )
        scope = run_example(
            "version-2.md", "## Reshaping: `concat` and `explode`"
        )
        self.assertEqual(scope["padded"]["b"].to_list(), [4, 5, None])

    def test_explode_empty_list_produces_no_row_by_default(self):
        scope = run_example(
            "version-2.md", "## Reshaping: `concat` and `explode`", block=1
        )
        self.assertEqual(scope["df"].explode("a")["a"].to_list(), [1, 2, 3])
        self.assertEqual(
            scope["df"].explode("a", empty_as_null=True)["a"].to_list(),
            [1, 2, None, 3],
        )

    def test_signed_plus_uint64_is_int128(self):
        scope = run_example("version-2.md", "## Casts and dtypes")
        combined = scope["lf"].select((pl.col("a") + pl.col("b")).alias("r"))
        self.assertEqual(combined.collect_schema()["r"], pl.Int128)
        with self.assertRaises(pl.exceptions.InvalidOperationError):
            pl.Series([1]).is_in(pl.Series([1.99]).implode())

    def test_selector_with_column_is_elementwise(self):
        scope = run_example(
            "version-2.md", "## Expressions and selectors"
        )
        self.assertEqual(scope["selected"].columns, ["mask"])
        # The 2.0 behavior is silent: this computes three columns.
        surprising = scope["df"].select(
            pl.selectors.integer() & pl.col("mask")
        )
        self.assertEqual(surprising.columns, ["a", "b", "mask"])

    def test_bin_quantiles_tolerates_ties(self):
        scope = run_example(
            "version-2.md", "## Expressions and selectors", block=1
        )
        self.assertEqual(scope["binned"]["bucket"].null_count(), 0)

    def test_sql_resolution_is_deferred(self):
        scope = run_example("version-2.md", "## SQL")
        with self.assertRaises(pl.exceptions.SQLInterfaceError):
            scope["ctx"].execute("SELECT * FROM derived").collect_schema()

    def test_string_to_temporal_cast_is_removed(self):
        with self.assertRaises(pl.exceptions.InvalidOperationError):
            pl.Series(["2022-08-30"]).cast(pl.Date)
        self.assertEqual(
            pl.Series(["2022-08-30"]).str.to_date().to_list(),
            [date(2022, 8, 30)],
        )


class ContextExamples(unittest.TestCase):
    def test_select_projects_and_broadcasts_scalars(self):
        frame = pl.DataFrame(
            {
                "name": ["a", "b", "c"],
                "revenue": [100, 50, 25],
                "cost": [40, 30, 30],
                "age": [30, 40, 50],
            }
        ).lazy()
        result = example("contexts.md", "## select()", lf=frame)
        self.assertIsInstance(result, pl.LazyFrame)
        # Both scalars broadcast to the three rows.
        self.assertEqual(
            result.collect().to_dicts(),
            [
                {"name": "a", "profit": 60, "avg_age": 40.0, "target": 25},
                {"name": "b", "profit": 20, "avg_age": 40.0, "target": 25},
                {"name": "c", "profit": -5, "avg_age": 40.0, "target": 25},
            ],
        )

    def test_with_columns_adds_replaces_and_aliases(self):
        frame = pl.DataFrame(
            {
                "quantity": [2, 3, 1],
                "price": [10, 20, 100],
                "date": [date(2024, 1, 15), date(2024, 2, 20), date(2024, 3, 5)],
                "status": ["active", "pending", "inactive"],
            }
        ).lazy()
        result = example("contexts.md", "## with_columns()", lf=frame).collect()
        self.assertEqual(result.columns, [
            "quantity", "price", "date", "status", "total", "year", "is_active",
        ])
        self.assertEqual(result["total"].to_list(), [20, 60, 100])
        self.assertEqual(result.schema["price"], pl.Float64)
        self.assertEqual(result["year"].to_list(), [2024, 2024, 2024])
        self.assertEqual(result["is_active"].to_list(), [True, False, False])

    def test_filter_is_inclusive_and_drops_null_rows(self):
        frame = pl.DataFrame(
            {
                "age": [17, 18, 30, 40],
                "status": ["active", "active", "pending", "active"],
                "date": [date(2024, 1, 1), date(2024, 1, 2),
                         date(2024, 1, 3), date(2024, 1, 4)],
                "is_deleted": [False, False, True, False],
            }
        ).lazy()
        result = example(
            "contexts.md", "## filter()", lf=frame,
            start=date(2024, 1, 1), end=date(2024, 1, 10),
        ).collect()
        # 17 fails the age gate, 30 is pending on a deleted row, 40 passes all.
        self.assertEqual(result["age"].to_list(), [18, 40])

        values = pl.DataFrame({"v": [20, None, 5]})
        self.assertEqual(values.filter(pl.col("v") > 10)["v"].to_list(), [20])
        self.assertEqual(
            values.filter(
                (pl.col("v") > 10) | pl.col("v").is_null()
            )["v"].to_list(),
            [20, None],
        )

    def test_group_by_expression_keys(self):
        frame = pl.DataFrame(
            {
                "date": [date(1995, 1, 1), date(2003, 1, 1), date(2004, 1, 1)],
                "height": [1.80, 1.65, 1.70],
            }
        ).lazy()
        result = example(
            "contexts.md", "## group_by() + agg()", block=1, lf=frame
        ).collect()
        # 1995 -> 1990; 2003 and 2004 both -> 2000, split by the height key.
        self.assertEqual(
            sorted(result.to_dicts(),
                   key=lambda r: (r["decade"], r["is_short"])),
            [
                {"decade": 1990, "is_short": False, "len": 1},
                {"decade": 2000, "is_short": False, "len": 1},
                {"decade": 2000, "is_short": True, "len": 1},
            ],
        )


class LazyApiExamples(unittest.TestCase):
    def test_dataframe_in_frame_out_keeps_the_signature(self):
        scope = run_example("lazy-api.md", "## Starting from an in-memory DataFrame")
        top_regions = scope["top_regions"]
        orders = pl.read_csv(FILES / "orders.csv")
        result = top_regions(orders, 2)
        self.assertIsInstance(result, pl.DataFrame)
        # South is absent: `orders.csv` has East, West, East, North.
        self.assertEqual(result.to_dicts(), [
            {"region": "North", "total": 300},
            {"region": "West", "total": 250},
        ])
        self.assertEqual(top_regions(orders, 4).height, 3)

    def test_schema_discovery_does_not_collect(self):
        lf = pl.scan_csv(FILES / "sales.csv")
        schema = lf.collect_schema()
        self.assertEqual(schema.names(), ["date", "revenue"])
        self.assertEqual(schema["revenue"], pl.Int64)
        self.assertEqual(lf.head(5).collect().height, 3)

    def test_scan_options_create_real_nulls_and_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dirty.csv"
            path.write_text("zipcode,n,seen\n01234,N/A,2024-01-15\n")
            lf = pl.scan_csv(
                path,
                null_values=["N/A", "NULL", ""],
                try_parse_dates=True,
                schema_overrides={"zipcode": pl.String},
            )
            row = lf.collect().to_dicts()[0]
        self.assertEqual(row["zipcode"], "01234")
        self.assertIsNone(row["n"])
        self.assertEqual(row["seen"], date(2024, 1, 15))

    def test_filter_pushes_down_into_the_scan(self):
        lf = pl.scan_csv(FILES / "orders.csv").select("region", "revenue")
        plan = lf.filter(pl.col("revenue") > 100).explain()
        self.assertIn("SELECTION", plan)
        self.assertIn("PROJECT 2/3 COLUMNS", plan)
        # A filter on a computed column cannot push into the scan node.
        computed = lf.with_columns(
            doubled=pl.col("revenue") * 2
        ).filter(pl.col("doubled") > 100).explain()
        self.assertNotEqual(plan, computed)

    def test_collect_all_returns_one_frame_per_query(self):
        # The doc block scans `orders/*.parquet`; the same shape over the
        # fixture CSV checks the promise: one base, several queries, one call.
        base = pl.scan_csv(FILES / "orders.csv").filter(pl.col("revenue") > 0)
        by_region = base.group_by("region").agg(pl.col("revenue").sum())
        by_month = base.group_by(
            pl.col("order_date").str.to_date().dt.month()
        ).agg(pl.col("revenue").sum())
        region_df, month_df = pl.collect_all([by_region, by_month])
        self.assertIsInstance(region_df, pl.DataFrame)
        self.assertEqual(region_df.sort("region").to_dicts(), [
            {"region": "East", "revenue": 175},
            {"region": "North", "revenue": 300},
            {"region": "West", "revenue": 250},
        ])
        self.assertEqual(month_df.sort("order_date").to_dicts(), [
            {"order_date": 1, "revenue": 100},
            {"order_date": 2, "revenue": 250},
            {"order_date": 3, "revenue": 375},
        ])


if __name__ == "__main__":
    unittest.main()
