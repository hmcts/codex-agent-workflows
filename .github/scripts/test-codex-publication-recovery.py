#!/usr/bin/env python3

from publisher_test_support import *  # noqa: F403


class CodexPublicationRecoveryTests(PublisherTestCase):
    def test_jira_publisher_rejects_incompatible_recovered_pr_identity(self) -> None:
        cases = {
            "wrong base": {"pr_base_ref": "develop"},
            "wrong base SHA": {"pr_base_sha": MOVED_SHA},
            "wrong base repository": {"pr_base_repository": "hmcts/other"},
            "fork head": {"pr_head_repository": "contributor/example"},
            "wrong head ref": {"pr_head_ref": "codex/other"},
            "ready instead of draft": {"expected_draft": True},
            "draft instead of ready": {"pr_is_draft": True},
            "multiple pull requests": {"multiple_prs": True},
        }
        for case, options in cases.items():
            with self.subTest(case=case):
                completed, commands, outputs = self.run_jira_with_outputs(
                    mode="initial",
                    remote_head=HEAD_SHA,
                    pr_head_sha=HEAD_SHA,
                    **options,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assert_no_push(commands)
                self.assertIn("Unable to recover exact pull request state", completed.stderr)
                self.assertNotIn("pr_url=", outputs)

    def test_publication_code_does_not_dispatch_pr_tasks(self) -> None:
        publication_paths = [JIRA_PUBLISHER, REVIEW_PUBLISHER]
        publication_paths.extend(
            sorted((SCRIPT_DIR.parent / "workflows").glob("codex*.y*ml"))
        )
        forbidden_markers = (
            "workflow run on-pr.yml",
            "/actions/workflows/on-pr.yml/dispatches",
        )

        for path in publication_paths:
            source = path.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                with self.subTest(path=path, marker=marker):
                    self.assertNotIn(marker, source)

    def test_jira_initial_publish_recovers_exact_branch_and_creates_missing_pr(self) -> None:
        completed, commands, outputs = self.run_jira_with_outputs(
            mode="initial",
            remote_head=HEAD_SHA,
            pr_exists=False,
            pr_head_sha=HEAD_SHA,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_no_push(commands)
        self.assertIn(f"commit_sha={HEAD_SHA}\n", outputs)
        self.assertIn("pr_url=https://github.com/hmcts/example/pull/42\n", outputs)

    def test_jira_recovery_rechecks_default_after_tree_validation(self) -> None:
        cases = (
            ("initial", HEAD_SHA, BASE_SHA, HEAD_SHA),
            ("repair", NEW_SHA, HEAD_SHA, NEW_SHA),
        )
        for mode, remote_head, remote_parent, pr_head_sha in cases:
            for freshness_case, final_base in (
                ("moved", MOVED_SHA),
                ("unavailable", ""),
            ):
                with self.subTest(mode=mode, freshness_case=freshness_case):
                    completed, commands, outputs = self.run_jira_with_outputs(
                        mode=mode,
                        remote_base=[BASE_SHA, final_base],
                        remote_head=remote_head,
                        remote_parent=remote_parent,
                        pr_head_sha=pr_head_sha,
                    )

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("Default branch", completed.stderr)
                    self.assertIn(f"rev-parse {remote_head}^{{tree}}", commands)
                    self.assert_no_push(commands)
                    self.assertNotIn("commit_sha=", outputs)

    def test_initial_empty_lease_rejects_atomic_branch_creation(self) -> None:
        self.assert_empty_lease_rejects_atomic_race("codex/atomic-initial")

    def test_jira_repair_exact_lease_rejects_atomic_branch_movement(self) -> None:
        self.assert_exact_lease_rejects_atomic_race("codex/atomic-repair")



if __name__ == "__main__":
    unittest.main()
