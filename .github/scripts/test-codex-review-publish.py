#!/usr/bin/env python3

from publisher_test_support import *  # noqa: F403


class CodexReviewPublishTests(PublisherTestCase):
    def test_review_publisher_accepts_exact_verified_head(self) -> None:
        completed, _ = self.run_review(HEAD_SHA)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_review_publisher_persists_push_outputs_before_comment_failure(self) -> None:
        completed, commands, outputs, _ = self.run_review_with_outputs(
            HEAD_SHA,
            fail_pr_comment=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assert_command_logged(commands, "push")
        self.assertIn("pr_number=42\n", outputs)
        self.assertIn("branch_name=codex/example\n", outputs)
        self.assertIn(f"commit_sha={NEW_SHA}\n", outputs)

    def test_review_publisher_rejects_moved_head(self) -> None:
        completed, _ = self.run_review(MOVED_SHA)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Existing review branch mismatch", completed.stderr)

    def test_review_publisher_rejects_moved_or_unavailable_default_before_push(self) -> None:
        for case, remote_base in {
            "moved": MOVED_SHA,
            "unavailable": "",
        }.items():
            with self.subTest(case=case):
                completed, commands, _, _ = self.run_review_with_outputs(
                    HEAD_SHA,
                    remote_base=remote_base,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("Default branch", completed.stderr)
                self.assert_command_logged(commands, "commit")
                self.assert_no_push(commands)

    def test_review_publisher_recovers_exact_previously_pushed_commit(self) -> None:
        completed, commands, outputs, comment = self.run_review_with_outputs(
            NEW_SHA,
            remote_parent=HEAD_SHA,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_no_push(commands)
        self.assertIn(f"commit_sha={NEW_SHA}\n", outputs)
        self.assertIn("Manual verification required", comment)

    def test_review_publisher_rejects_recovery_with_wrong_tree(self) -> None:
        completed, commands, _, _ = self.run_review_with_outputs(
            NEW_SHA,
            remote_parent=HEAD_SHA,
            remote_tree=OTHER_TREE_SHA,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Existing review branch mismatch", completed.stderr)
        self.assert_no_push(commands)



if __name__ == "__main__":
    unittest.main()
