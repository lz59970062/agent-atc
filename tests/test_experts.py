"""Expert agents: a portable experts file, and reins agent expert <name>."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from harness_reins.commands import experts as expert_commands
from harness_reins.core import experts as expert_rules
from harness_reins.core import ids
from harness_reins.engine import dispatch
from harness_reins.store import layout
from test_contexts import ContextTests

HARNESSES = {"codex": ("low", "high"), "claude": (), "pi": ("off", "high")}


class ExpertRulesTests(unittest.TestCase):
    def test_experts_are_validated_and_instruction_files_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "prompts").mkdir()
            (base / "prompts" / "writer.md").write_text("You write clearly.\n", encoding="utf-8")
            experts = expert_rules.validate({"schema": "reins.experts/1", "experts": {
                "writer": {"harness": "claude", "model": "opus", "instructions_file": "prompts/writer.md"},
                "reviewer": {"harness": "codex", "reasoning": "high", "instructions": "Review hard.", "report": False},
            }}, base=base, harnesses=HARNESSES)
        self.assertEqual(experts["writer"]["instructions"], "You write clearly.")
        self.assertFalse(experts["reviewer"]["report"])
        for bad, reason in (({"experts": {}}, "schema"),
                            ({"schema": "reins.experts/1", "experts": {"x": {"harness": "gemini"}}}, "harness must be"),
                            ({"schema": "reins.experts/1", "experts": {"x": {"harness": "claude", "reasoning": "high"}}}, "reasoning"),
                            ({"schema": "reins.experts/1", "experts": {"x": {"harness": "codex", "colour": "red"}}}, "unknown fields"),
                            ({"schema": "reins.experts/1", "experts": {"bad name": {"harness": "codex"}}}, "names use")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                expert_rules.validate(bad, base=None, harnesses=HARNESSES)

    def test_instructions_lead_the_task_prompt(self):
        self.assertEqual(expert_rules.prompt({"instructions": "Be terse."}, "Summarize X."), "Be terse.\n\n## Task\n\nSummarize X.")
        self.assertEqual(expert_rules.prompt({"instructions": None}, "Summarize X."), "Summarize X.")


class ExpertFileTests(unittest.TestCase):
    def run_command(self, home: Path, action: str, **options) -> tuple[int, str]:
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"REINS_HOME": str(home)}), contextlib.redirect_stdout(output):
            status = expert_commands.command(argparse.Namespace(expert_action=action, experts=None, **options))
        return status, output.getvalue()

    def test_export_is_self_contained_and_imports_on_another_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "src" / "prompts").mkdir(parents=True)
            (tmp / "src" / "prompts" / "writer.md").write_text("Line one.\nLine two.\n", encoding="utf-8")
            (tmp / "src" / "experts.yaml").write_text(yaml.safe_dump({"schema": "reins.experts/1", "experts": {
                "writer": {"harness": "claude", "model": "opus", "description": "Writes",
                           "instructions_file": "prompts/writer.md"}}}), encoding="utf-8")
            first, second = tmp / "machine-a", tmp / "machine-b"
            self.assertEqual(self.run_command(first, "import", file=str(tmp / "src" / "experts.yaml"), replace=False)[0], 0)
            status, exported = self.run_command(first, "export", names=[])
            self.assertEqual(status, 0)
            self.assertIn("instructions: |", exported)
            self.assertNotIn("instructions_file", exported)
            portable = tmp / "portable.yaml"
            portable.write_text(exported, encoding="utf-8")
            self.assertEqual(self.run_command(second, "import", file=str(portable), replace=False)[0], 0)
            self.assertEqual(self.run_command(second, "export", names=[])[1], exported)
            status, listing = self.run_command(second, "list")
            self.assertIn("writer  claude (opus)  Writes", listing)
            self.assertEqual(self.run_command(second, "show", name="missing")[0], 2)


class ExpertAgentTests(ContextTests):
    harness = "claude"
    # The inherited context cases already run in test_contexts.
    test_continue_inherits_context_and_fork_branches_from_an_exact_checkpoint = None
    test_every_node_is_named = None

    def test_an_expert_runs_its_harness_with_its_instructions_and_lineage_keeps_it(self):
        experts_file = self.directory / "experts.yaml"
        experts_file.write_text(yaml.safe_dump({"schema": "reins.experts/1", "experts": {
            "writer": {"harness": "claude", "model": "opus", "instructions": "You write release notes."}}}),
            encoding="utf-8")
        draft = self.submit("expert", "Draft notes for 0.8.", expert="writer", experts=str(experts_file), name="draft")
        self.assertEqual((draft["agent_worker"], draft["child_model"], draft["expert"]), ("claude", "opus", "writer"))
        self.assertEqual(self.run_node(draft), ["You write release notes.\n\n## Task\n\nDraft notes for 0.8."])
        callback = dispatch.load_request(layout.request_path(self.root, "pending", ids.callback_id(str(draft["id"]))))
        self.assertIn("From: agent claude, expert writer, model opus, node draft", callback["prompt_compact"])
        polish = self.submit("continue", "Make it shorter.", node="draft", name="polish")
        self.assertEqual(polish["expert"], "writer")
        self.assertEqual(self.run_node(polish)[-1], "Make it shorter.", "instructions are not repeated on continue")


del ContextTests  # imported only as a base; do not run its cases twice


if __name__ == "__main__":
    unittest.main()
