from __future__ import annotations

import argparse
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_reins.core import ids
from harness_reins.engine import dispatch
from harness_reins.store import layout


class IdentifierTests(unittest.TestCase):
    def test_children_name_their_kind_and_ordinal_under_one_root(self):
        self.assertEqual(ids.callback_id("1a2b3c4d"), "1a2b3c4d-c1")
        self.assertEqual(ids.callback_id("1a2b3c4d", 3), "1a2b3c4d-c3")
        self.assertEqual(ids.attempt_id("1a2b3c4d", 2), "1a2b3c4d-a2")
        self.assertEqual(ids.callback_id(ids.goal_root("stage-goal"), 2), "g-stage-goal-c2")

    def test_root_of_strips_only_a_kind_suffix(self):
        self.assertEqual(ids.root_of("1a2b3c4d-c12"), "1a2b3c4d")
        self.assertEqual(ids.root_of("1a2b3c4d-a1"), "1a2b3c4d")
        self.assertEqual(ids.root_of("g-my-cat-c2"), "g-my-cat")
        self.assertEqual(ids.root_of("1a2b3c4d"), "1a2b3c4d")
        self.assertEqual(ids.root_of("g-my-cat"), "g-my-cat")

    def test_invalid_roots_and_ordinals_are_refused(self):
        for root, sequence in (("bad/root", 1), ("", 1), ("1a2b3c4d", 0), ("1a2b3c4d", True), ("1a2b3c4d", 1.0)):
            with self.subTest(root=root, sequence=sequence), self.assertRaises(ValueError):
                ids.callback_id(root, sequence)

    def test_new_roots_skip_roots_already_taken(self):
        with mock.patch.object(ids.secrets, "token_hex", side_effect=["aaaaaaaa", "bbbbbbbb"]):
            self.assertEqual(ids.new_root(lambda root: root == "aaaaaaaa"), "bbbbbbbb")
        self.assertRegex(ids.new_root(), r"^[0-9a-f]{8}$")

    def test_external_callbacks_get_a_fresh_unused_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            layout.managed_task_dir(queue, "aaaaaaaa").mkdir(parents=True)
            (queue / "done").mkdir(parents=True)
            (queue / "done" / "bbbbbbbb-c1.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(ids.secrets, "token_hex", side_effect=["aaaaaaaa", "bbbbbbbb", "cccccccc"]):
                self.assertEqual(dispatch.new_callback_id(queue), "cccccccc-c1")

    def test_done_callback_ids_are_a_root_and_its_first_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(queue_dir=tmp, cwd=tmp, agent="codex", session="parent", task="t",
                                      exit_code=0, message=None, callback_mode="manual")
            with mock.patch("sys.stderr"):
                request = dispatch.make_request(args, "prompt")
        self.assertRegex(request["id"], r"^[0-9a-f]{8}-c1$")
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", request["id"]))


if __name__ == "__main__":
    unittest.main()
