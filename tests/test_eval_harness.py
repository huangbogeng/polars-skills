"""Keep the eval tasks solvable and the reference solution in sync."""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EvalHarnessTest(unittest.TestCase):
    def test_reference_solution_passes_every_task(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "evals" / "run_eval.py"),
                "--solution",
                str(ROOT / "evals" / "reference_solution.py"),
                "--json",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"passed": 5', result.stdout)


if __name__ == "__main__":
    unittest.main()
