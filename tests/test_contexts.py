"""Contexts and nodes: continue and fork child agents by ATC names, for every harness.

The fake harnesses keep a session as a history file. A new session holds the
prompt; forking copies the parent's history and appends the new prompt; the
child's answer is the history it saw. So an answer shows exactly what context
a node inherited.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from agent_atc.commands import work as work_commands
from agent_atc.core import ids
from agent_atc.engine import contexts as contexts_engine
from agent_atc.engine import dispatch
from agent_atc.engine import tasks as managed_tasks
from agent_atc.engine import worker as task_worker
from agent_atc.store import contexts, layout
from test_cli import executable_python_fixture, patch_fixture_commands

FAKE = r'''#!{python}
import json, os, sys, uuid
from pathlib import Path
store = Path(os.environ["FAKE_SESSIONS"]); store.mkdir(parents=True, exist_ok=True)
argv = sys.argv[1:]
prompt = sys.stdin.read().split("\n\nBefore you finish", 1)[0]
def history(ref):
    path = Path(ref) if os.path.isabs(ref) else store / f"{{ref}}.json"
    return json.loads(path.read_text())
harness = "{harness}"
if harness == "codex":
    parent = argv[argv.index("fork") + 1] if "fork" in argv else None
    sid, path = str(uuid.uuid4()), None
    print(json.dumps({{"type": "thread.started", "thread_id": sid}}), flush=True)
elif harness == "claude":
    parent = argv[argv.index("--resume") + 1] if "--resume" in argv else None
    sid, path = argv[argv.index("--session-id") + 1], None
else:
    parent = argv[argv.index("--fork") + 1] if "--fork" in argv else None
    sid = str(uuid.uuid4())
    path = Path(argv[argv.index("--session-dir") + 1]) / f"{{sid}}.jsonl"
seen = (history(parent) if parent else []) + [prompt]
(path or store / f"{{sid}}.json").write_text(json.dumps(seen))
answer = json.dumps(seen)
if harness == "codex":
    Path(argv[argv.index("-o") + 1]).write_text(answer)
else:
    sys.stdout.write(answer)
'''


class ContextTests(unittest.TestCase):
    harness = "codex"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "queue"
        self.environment = mock.patch.dict(os.environ, {
            "ATC_HOME": str(self.directory / "atc-home"), "FAKE_SESSIONS": str(self.directory / "sessions"),
            f"ATC_{self.harness.upper()}_BIN": str(self.fake()),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        patch_fixture_commands(self)

    def fake(self) -> Path:
        script = self.directory / f"fake-{self.harness}"
        script.write_text(FAKE.format(python=sys.executable, harness=self.harness), encoding="utf-8")
        return executable_python_fixture(script)

    def args(self, worker: str, prompt: str, **options) -> argparse.Namespace:
        values = dict(cwd=str(self.directory), task="long task", command=None, exit_code=None, message=None,
                      session="parent-thread", via_daemon=False, queue_dir=str(self.root), approvals_reviewer="auto_review",
                      approval_policy="on-request", sandbox_mode="workspace-write", dry_run=False, strict=False,
                      wrapped_command=[], agent="codex", agent_worker=worker, agent_prompt=["--", prompt],
                      permission_mode="auto", no_report=True, name=None, node=None)
        values.update(options)
        return argparse.Namespace(**values)

    def submit(self, worker: str, prompt: str, **options) -> dict[str, object]:
        with mock.patch.object(managed_tasks, "screen_binary", return_value="/usr/bin/screen"), mock.patch("sys.stderr"):
            self.assertEqual(work_commands.agent(self.args(worker, prompt, **options)), 0)
        task_id = max(layout.managed_tasks_root(self.root).glob("*/task.json"), key=lambda p: p.stat().st_mtime).parent.name
        return managed_tasks.load_managed_task(layout.managed_task_path(self.root, task_id))

    def run_node(self, task: dict[str, object]) -> list[str]:
        task["state"] = "launching"
        managed_tasks.write_managed_task(self.root, task)
        path = layout.managed_task_path(self.root, str(task["id"]))
        with mock.patch.dict(os.environ, {"STY": "screen-session"}), mock.patch("sys.stdout"):
            self.assertEqual(task_worker.run_screen_worker(argparse.Namespace(task_file=str(path), token=task["token"])), 0)
        return json.loads(Path(str(task["agent_result_path"])).read_text(encoding="utf-8"))

    def refused(self, worker: str, prompt: str, expected: str, **options) -> None:
        with mock.patch.object(managed_tasks, "screen_binary", return_value="/usr/bin/screen"), \
                self.assertRaises(SystemExit) as caught:
            work_commands.agent(self.args(worker, prompt, **options))
        self.assertIn(expected, str(caught.exception))

    def test_continue_inherits_context_and_fork_branches_from_an_exact_checkpoint(self):
        research = self.submit(self.harness, "investigate X", name="research")
        self.assertEqual(research["node"]["name"], "research")
        self.refused("continue", "too early", "has not finished", node="research", name="early")
        self.assertEqual(self.run_node(research), ["investigate X"])
        research = managed_tasks.load_managed_task(layout.managed_task_path(self.root, str(research["id"])))
        self.assertIsNotNone(research["node"]["session"])
        result = contexts_engine.node_result(self.root, research)
        self.assertEqual(result["worker"]["session"], research["node"]["session"])
        self.assertEqual(result["node"], "research")
        callback = dispatch.load_request(layout.request_path(self.root, "pending", ids.callback_id(str(research["id"]))))
        self.assertIn(f"From: agent {self.harness}, node research", callback["prompt_compact"])

        self.assertEqual(contexts_engine.resolve(self.root, ids.callback_id(str(research["id"])))["id"], research["id"])
        review = self.submit("continue", "write a review", node="research", name="review")
        self.assertEqual(review["node"]["name"], "research/review")
        self.assertEqual(review["node"]["lineage"], {"from": research["id"], "mode": "continue"})
        self.assertEqual(review["agent_worker"], self.harness)
        self.assertEqual(self.run_node(review), ["investigate X", "write a review"])

        self.refused("continue", "again", "not the end of its chain", node="research", name="again")
        plan = self.submit("fork", "make a plan", node=str(research["id"]), name="plan")
        self.assertEqual(plan["node"]["name"], "research/plan")
        self.assertEqual(self.run_node(plan), ["investigate X", "make a plan"], "a fork sees its node, not later steps")
        again = self.submit("fork", "plan again", node="research", name="plan2")
        self.assertEqual(self.run_node(again), ["investigate X", "plan again"], "an earlier fork does not leak either")

        chain = contexts.load(str(research["id"]))
        self.assertEqual(chain["nodes"], [research["id"], review["id"]])
        branch = contexts.load(str(plan["id"]))
        self.assertEqual(branch["parent"], {"context": research["id"], "node": research["id"]})
        self.refused("fork", "dup", "already exists", node="research", name="plan")

    def test_claude_callback_hint_follows_background_support(self):
        import contextlib, io
        for number, (environment, expected) in enumerate((({}, "run in the background (run_in_background)"),
                                                          ({"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}, "end your turn"))):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment), \
                    mock.patch.object(managed_tasks, "screen_binary", return_value="/usr/bin/screen"), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                work_commands.agent(self.args(self.harness, "x", name=f"hint{number}", agent="claude"))
            self.assertIn(expected, errors.getvalue())

    def test_every_node_is_named(self):
        self.refused(self.harness, "unnamed", "--name")
        self.refused(self.harness, "bad", "cannot contain '/'", name="a/b")
        task = self.submit(self.harness, "titled", task="audit the parser deeply now please")
        self.assertEqual(task["node"]["name"], "audit-the-parser-deeply")


class ClaudeContextTests(ContextTests):
    harness = "claude"


class PiContextTests(ContextTests):
    harness = "pi"


if __name__ == "__main__":
    unittest.main()


class ContextViewTests(ContextTests):
    """atc context list/show give summaries and never print a raw session."""

    def view(self, function, **options) -> str:
        import contextlib, io
        from agent_atc.commands import contexts as context_commands
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = getattr(context_commands, function)(argparse.Namespace(queue_dir=str(self.root), **options))
        self.assertEqual(status, 0)
        return output.getvalue()

    def test_views_show_summaries_and_session_locations_only(self):
        research = self.submit(self.harness, "investigate X in depth", name="research")
        self.run_node(research)
        review = self.submit("continue", "write a review", node="research", name="review")
        self.run_node(review)
        plan = self.submit("fork", "make a plan", node="research", name="plan")
        listing = self.view("list_contexts", all=False, json=False)
        self.assertIn(f"context {research['id']} · {self.harness}", listing)
        self.assertIn("research/review", listing)
        self.assertIn(f"forked from research ({research['id']})", listing)
        self.assertIn("submitted", listing)  # the fork has not run yet
        shown = self.view("show_context", node="research/review", json=False)
        self.assertIn("task: write a review", shown)
        self.assertIn("result: exited 0", shown)
        self.assertIn("(not printed; read the report instead)", shown)
        self.assertNotIn('["investigate X in depth", "write a review"]', shown, "the raw session stays unprinted")
        self.assertIn("Continue: atc agent continue research/review", shown)
        data = json.loads(self.view("list_contexts", all=False, json=True))
        self.assertEqual([len(v["nodes"]) for v in data], [2, 1])
        self.assertEqual(self.view("archive_context", node="research/plan").count("Archived"), 1)
        self.assertNotIn("research/plan", self.view("list_contexts", all=False, json=False))
        self.assertIn("research/plan", self.view("list_contexts", all=True, json=False))


class AgentCommandLineTests(unittest.TestCase):
    """The real parser: options after a node or expert name must not become prompt text."""

    def parse(self, *argv: str) -> argparse.Namespace:
        from agent_atc import cli
        captured = {}
        def fake_agent(args):
            captured["args"] = args
            return 0
        with mock.patch.object(work_commands, "agent", side_effect=fake_agent), \
                mock.patch.object(sys, "argv", ["atc", *argv]), mock.patch("sys.stderr"):
            self.assertEqual(cli.main(), 0)
        return captured["args"]

    def test_lineage_and_expert_options_are_parsed_before_the_prompt(self):
        for argv, positional in ((["agent", "continue", "research"], "node"), (["agent", "fork", "1a2b3c4d-c1"], "node"),
                                 (["agent", "expert", "writer"], "expert")):
            with self.subTest(argv=argv):
                args = self.parse(*argv, "--session", "s1", "--name", "review", "--no-report", "--",
                                  "Review", "--name", "inside the prompt")
                self.assertEqual(getattr(args, positional), argv[-1])
                self.assertEqual(args.name, "review")
                self.assertTrue(args.no_report)
                self.assertEqual(managed_tasks.agent_prompt_text(list(args.agent_prompt)), "Review --name inside the prompt")

    def test_harness_subcommands_keep_their_prompt_after_the_separator(self):
        args = self.parse("agent", "claude", "--session", "s1", "--name", "research", "--", "Survey", "X")
        self.assertEqual((args.agent_worker, args.name), ("claude", "research"))
        self.assertEqual(managed_tasks.agent_prompt_text(list(args.agent_prompt)), "Survey X")
