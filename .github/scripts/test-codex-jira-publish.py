#!/usr/bin/env python3

from publisher_test_support import *  # noqa: F403


class CodexJiraPublishTests(PublisherTestCase):
    def test_jira_publisher_loads_notifier_from_shared_runtime(self) -> None:
        completed, _ = self.run_jira(mode="initial")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_jira_publisher_persists_pr_outputs_before_notify_failure(self) -> None:
        completed, commands, outputs = self.run_jira_with_outputs(
            mode="initial",
            fail_notify=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assert_command_logged(commands, "push")
        self.assertIn("branch_name=codex/example\n", outputs)
        self.assertIn(f"commit_sha={NEW_SHA}\n", outputs)
        self.assertIn("pr_url=https://github.com/hmcts/example/pull/42\n", outputs)
        self.assertIn("pr_number=42\n", outputs)

    def test_jira_publisher_persists_branch_outputs_before_pr_create_failure(self) -> None:
        completed, commands, outputs = self.run_jira_with_outputs(
            mode="initial",
            pr_exists=False,
            fail_pr_create=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assert_command_logged(commands, "push")
        self.assertIn("branch_name=codex/example\n", outputs)
        self.assertIn(f"commit_sha={NEW_SHA}\n", outputs)
        self.assertNotIn("pr_url=", outputs)

    def test_jira_publisher_rejects_pr_with_wrong_head_identity(self) -> None:
        completed, commands, outputs = self.run_jira_with_outputs(
            mode="initial",
            pr_head_sha=MOVED_SHA,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assert_command_logged(commands, "push")
        self.assertIn("does not match exact recovery state", completed.stderr)
        self.assertNotIn("pr_url=", outputs)
        self.assertNotIn("pr_number=", outputs)

    def test_jira_initial_publish_requires_absent_branch(self) -> None:
        completed, commands = self.run_jira(mode="initial")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "push --force-with-lease=refs/heads/codex/example: "
            "--set-upstream origin codex/example",
            commands,
        )

    def test_jira_initial_publish_rejects_existing_branch_with_wrong_tree(self) -> None:
        completed, commands = self.run_jira(
            mode="initial",
            remote_head=HEAD_SHA,
            remote_tree=OTHER_TREE_SHA,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Existing generated branch mismatch", completed.stderr)
        self.assert_no_push(commands)

    def test_jira_publisher_rejects_moved_base(self) -> None:
        completed, _ = self.run_jira(mode="initial", remote_base=MOVED_SHA)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Default branch moved", completed.stderr)

    def test_jira_publisher_rechecks_default_immediately_before_each_push(self) -> None:
        for mode, remote_head in (("initial", ""), ("repair", HEAD_SHA)):
            with self.subTest(mode=mode):
                completed, commands = self.run_jira(
                    mode=mode,
                    remote_base=[BASE_SHA, MOVED_SHA],
                    remote_head=remote_head,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("Default branch moved", completed.stderr)
                self.assert_command_logged(commands, "commit")
                self.assert_no_push(commands)

    def test_jira_publisher_fails_closed_when_default_lookup_fails_before_push(self) -> None:
        completed, commands = self.run_jira(
            mode="initial",
            remote_base=[BASE_SHA, ""],
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Default branch unavailable", completed.stderr)
        self.assert_command_logged(commands, "commit")
        self.assert_no_push(commands)

    def test_jira_publisher_rechecks_default_after_lookup_before_pr_create(self) -> None:
        completed, commands, outputs = self.run_jira_with_outputs(
            mode="initial",
            remote_base=[BASE_SHA, BASE_SHA, MOVED_SHA],
            pr_exists=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Default branch moved", completed.stderr)
        self.assert_command_logged(commands, "push")
        self.assertIn("gh api --paginate --slurp", commands)
        self.assertNotIn("gh pr create", commands)
        self.assertNotIn("pr_url=", outputs)

    def test_jira_publisher_rechecks_generated_branch_after_lookup_before_create(self) -> None:
        for case, post_push_head in (
            ("moved", MOVED_SHA),
            ("unavailable", ""),
        ):
            with self.subTest(case=case):
                completed, commands, outputs = self.run_jira_with_outputs(
                    mode="initial",
                    remote_base=[BASE_SHA, BASE_SHA, BASE_SHA],
                    pr_exists=False,
                    post_push_head=post_push_head,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("Generated branch", completed.stderr)
                self.assert_command_logged(commands, "push")
                lookup = commands.index("gh api --paginate --slurp")
                final_branch_lookup = commands.rindex(
                    "ls-remote --exit-code --heads origin refs/heads/codex/example"
                )
                self.assertLess(lookup, final_branch_lookup)
                self.assertNotIn("gh pr create", commands)
                self.assertNotIn("pr_url=", outputs)

    def test_jira_repair_accepts_expected_head_and_uses_exact_lease(self) -> None:
        completed, commands = self.run_jira(mode="repair", remote_head=HEAD_SHA)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            f"push --force-with-lease=refs/heads/codex/example:{HEAD_SHA} "
            "--set-upstream origin codex/example",
            commands,
        )

    def test_jira_repair_rejects_intervening_commit(self) -> None:
        completed, commands = self.run_jira(
            mode="repair",
            remote_head=[HEAD_SHA, MOVED_SHA],
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("moved while the repaired commit was being prepared", completed.stderr)
        self.assert_command_logged(commands, "commit")
        self.assert_no_push(commands)



if __name__ == "__main__":
    unittest.main()
