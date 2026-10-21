"""Exercise the lift harness without spending model calls."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import lift_eval  # noqa: E402
import tasks  # noqa: E402


def load_module(path: Path, name: str = "candidate"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GradeTest(unittest.TestCase):
    def test_reference_solution_passes_every_task(self):
        reference = ROOT / "evals" / "reference_solution.py"
        for task in tasks.TASKS:
            with self.subTest(task=task.id):
                passed, error = lift_eval.grade(task, reference)
                self.assertTrue(passed, error)

    def test_missing_solution_is_a_failure_not_an_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            passed, error = lift_eval.grade(tasks.TASKS[0], Path(temp) / "solution.py")
        self.assertFalse(passed)
        self.assertIn("not created", error)

    def test_main_block_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "solution.py"
            path.write_text(
                "import polars as pl\n"
                "def top_n_per_group(lf):\n"
                "    return (\n"
                "        lf.group_by('region', 'product')\n"
                "        .agg(pl.col('revenue').sum().alias('total'))\n"
                "        .sort('region', 'total', 'product',\n"
                "              descending=[False, True, False])\n"
                "        .group_by('region', maintain_order=True).head(3)\n"
                "        .collect()\n"
                "    )\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit('must not run during grading')\n"
            )
            module = lift_eval.load_candidate(path)
        self.assertTrue(callable(module.top_n_per_group))


class SummaryTest(unittest.TestCase):
    def row(self, arm, passed, runs=1, tokens=100):
        return {
            "agent": "omp",
            "arm": arm,
            "task": "t",
            "passed": passed,
            "runs": runs,
            "rate": passed / runs,
            "mean_tokens": tokens,
        }

    def test_single_arm_has_no_lift(self):
        summary = lift_eval.lift_by_agent([self.row("with", 1)])
        omp = summary[0]
        self.assertIsNone(omp["without_rate"])
        self.assertIsNone(omp["lift"])
        self.assertIsNone(omp["token_delta"])
        self.assertEqual(omp["with_rate"], 1.0)

    def test_lift_and_token_delta_use_all_runs(self):
        rows = [
            self.row("without", 0, runs=2, tokens=40),
            self.row("with", 2, runs=2, tokens=100),
        ]
        omp = lift_eval.lift_by_agent(rows)[0]
        self.assertEqual(omp["without_rate"], 0.0)
        self.assertEqual(omp["with_rate"], 1.0)
        self.assertEqual(omp["lift"], 1.0)
        self.assertEqual(omp["token_delta"], 60)

    def test_prompt_names_the_solution_module_and_contract(self):
        prompt = lift_eval.build_prompt(tasks.TASKS[0])
        self.assertIn("solution.py", prompt)
        self.assertIn("Define top_n_per_group", prompt)


if __name__ == "__main__":
    unittest.main()
