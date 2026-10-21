"""Exercise the bundled live API lookup against the installed Polars."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import polars as pl


SCRIPT = Path(__file__).resolve().parents[1] / "polars" / "scripts" / "polars_api.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
    )


class ApiLookupTest(unittest.TestCase):
    def test_version_matches_installed_package(self):
        result = run("version", "--output", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["polars"], pl.__version__)

    def test_doc_shows_installed_signature(self):
        result = run("doc", "LazyFrame.join")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LazyFrame.join", result.stdout)
        self.assertIn("maintain_order", result.stdout)

    def test_nested_namespace_doc(self):
        result = run("doc", "Expr.str.contains")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Expr.str.contains", result.stdout)
        self.assertIn("literal", result.stdout)

    def test_browse_namespace(self):
        result = run("browse", "Expr.str", "--filter", "contains")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Expr.str.contains", result.stdout)

    def test_search_returns_json(self):
        result = run("search", "join", "--limit", "5", "--output", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = {entry["name"] for entry in json.loads(result.stdout)}
        self.assertIn("LazyFrame.join", names)

    def test_unknown_name_fails(self):
        result = run("doc", "NotARealPolarsName")
        self.assertEqual(result.returncode, 1)
        self.assertIn("NotARealPolarsName", result.stderr)


if __name__ == "__main__":
    unittest.main()
