"""Keep the Claude, Codex, and omp packaging paths consistent."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install_skill.py"
SYNC = ROOT / "scripts" / "sync_codex_plugin.py"
CODEX_PLUGIN = ROOT / "plugins" / "polars"
CODEX_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


class CrossAgentPackagingTest(unittest.TestCase):
    def test_codex_plugin_copy_matches_canonical_skill(self):
        result = subprocess.run(
            [sys.executable, str(SYNC), "--check"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((CODEX_PLUGIN / "skills" / "polars" / "SKILL.md").is_file())

    def test_codex_marketplace_points_at_a_real_plugin(self):
        catalog = json.loads(CODEX_MARKETPLACE.read_text())
        entry = next(item for item in catalog["plugins"] if item["name"] == "polars")
        plugin_root = ROOT / entry["source"]["path"].removeprefix("./")
        self.assertTrue((plugin_root / ".codex-plugin" / "plugin.json").is_file())

        manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text())
        skills = plugin_root / manifest["skills"].removeprefix("./")
        self.assertTrue((skills / "polars" / "SKILL.md").is_file())

        claude = json.loads((ROOT / "polars" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], claude["version"])

    def test_installer_serves_all_three_agents(self):
        with tempfile.TemporaryDirectory() as home:
            with tempfile.TemporaryDirectory() as project:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(INSTALL),
                        "--agents",
                        "all",
                        "--scope",
                        "both",
                        "--home",
                        home,
                        "--project",
                        project,
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for root in (Path(home), Path(project)):
                    claude = root / ".claude" / "skills" / "polars"
                    shared = root / ".agents" / "skills" / "polars"
                    self.assertTrue(claude.is_symlink(), claude)
                    self.assertTrue(shared.is_symlink(), shared)
                    self.assertTrue((claude / "SKILL.md").is_file())
                    self.assertTrue((shared / "SKILL.md").is_file())

    def test_installer_refuses_to_replace_without_force(self):
        with tempfile.TemporaryDirectory() as home:
            with tempfile.TemporaryDirectory() as project:
                dest = Path(home) / ".agents" / "skills" / "polars"
                dest.mkdir(parents=True)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(INSTALL),
                        "--agents",
                        "omp",
                        "--home",
                        home,
                        "--project",
                        project,
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("use --force", result.stderr)


if __name__ == "__main__":
    unittest.main()
