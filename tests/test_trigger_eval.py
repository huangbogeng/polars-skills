"""Check trigger detection against the event shapes each agent emits."""

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trigger_eval", ROOT / "evals" / "trigger_eval.py"
)
trigger_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trigger_eval)


def lines(*events):
    return "\n".join(json.dumps(event) for event in events)


class UsageExtractionTest(unittest.TestCase):
    def test_claude_result_usage_and_cost(self):
        output = lines(
            {
                "type": "result",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "total_cost_usd": 0.25,
            }
        )
        usage = trigger_eval.extract_usage("claude", output)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(usage["cost_usd"], 0.25)

    def test_codex_turn_usage(self):
        output = lines(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 200, "output_tokens": 30},
            }
        )
        usage = trigger_eval.extract_usage("codex", output)
        self.assertEqual(usage["total_tokens"], 230)
        self.assertIsNone(usage["cost_usd"])

    def test_omp_sums_assistant_messages(self):
        output = lines(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "user"},
                    {
                        "role": "assistant",
                        "usage": {
                            "input": 100,
                            "output": 10,
                            "totalTokens": 110,
                            "cost": 0.01,
                        },
                    },
                    {
                        "role": "assistant",
                        "usage": {
                            "input": 50,
                            "output": 5,
                            "totalTokens": 55,
                            "cost": 0.02,
                        },
                    },
                ],
            }
        )
        usage = trigger_eval.extract_usage("omp", output)
        self.assertEqual(usage["total_tokens"], 165)
        self.assertAlmostEqual(usage["cost_usd"], 0.03)


class TriggerDetectionTest(unittest.TestCase):
    def test_claude_skill_tool(self):
        output = lines(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "polars-probe"}}
                    ]
                },
            }
        )
        self.assertTrue(trigger_eval.triggered("claude", output, "polars-probe", Path("/tmp/probe")))


    def test_claude_answer_without_skill_is_quiet(self):
        output = lines(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Polars is a library."}]},
            }
        )
        self.assertFalse(trigger_eval.triggered("claude", output, "polars-probe", Path("/tmp/probe")))

    def test_codex_command_read(self):
        output = lines(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "cat /tmp/probe/SKILL.md",
                },
            }
        )
        self.assertTrue(trigger_eval.triggered("codex", output, "polars-probe", Path("/tmp/probe")))

    def test_codex_directory_listing_is_not_a_read(self):
        output = lines(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "find /tmp/probe -maxdepth 2",
                    "aggregated_output": "/tmp/probe/SKILL.md",
                },
            }
        )
        self.assertFalse(
            trigger_eval.triggered("codex", output, "polars-probe", Path("/tmp/probe"))
        )

    def test_codex_text_only_is_quiet(self):
        output = lines(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Use pl.scan_csv."},
            }
        )
        self.assertFalse(trigger_eval.triggered("codex", output, "polars-probe", Path("/tmp/probe")))

    def test_omp_skill_uri(self):
        output = lines(
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "args": {"path": "skill://polars-probe"},
            }
        )
        self.assertTrue(trigger_eval.triggered("omp", output, "polars-probe", Path("/tmp/probe")))

    def test_omp_listing_result_is_not_a_read(self):
        output = lines(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "result": {"content": "/tmp/probe/SKILL.md"},
            }
        )
        self.assertFalse(
            trigger_eval.triggered("omp", output, "polars-probe", Path("/tmp/probe"))
        )

    def test_omp_text_only_is_quiet(self):
        output = lines({"type": "message_end", "message": {"content": "Use lazy Polars."}})
        self.assertFalse(trigger_eval.triggered("omp", output, "polars-probe", Path("/tmp/probe")))


if __name__ == "__main__":
    unittest.main()
