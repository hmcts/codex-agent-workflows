#!/usr/bin/env python3

from workflow_safety_test_support import *  # noqa: F403


class WorkflowTriggerPolicyTests(WorkflowSafetyTestCase):
    def test_job_read_only_override_removes_inherited_workflow_write(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions:
  contents: write
jobs:
  test:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps: []"""
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_trusted_only_branch_push_may_use_credentials(self):
        for branches in ("[main]", "[main, 'release/**']"):
            with self.subTest(branches=branches):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions:
  id-token: write
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                            trigger=f"on:\n  push:\n    branches: {branches}",
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_tags_only_push_may_use_credentials(self):
        for filter_name in ("tags", "tags-ignore"):
            with self.subTest(filter_name=filter_name):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions: write-all
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                            trigger=f"on:\n  push:\n    {filter_name}: ['v*']",
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_full_generated_branch_exclusion_is_trusted_only(self):
        triggers = (
            "on:\n  push:\n    branches-ignore: ['codex/**']",
            "on:\n  push:\n    branches: ['**', '!codex/**']",
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions: write-all
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
                            trigger=trigger,
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_mixed_trigger_remains_protected_by_pull_request(self):
        self.assert_blocked(
            workflow(
                """permissions:
  contents: write
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on:\n  pull_request:\n  push:\n    branches: [main]",
            ),
            "effective write permission(s): contents",
        )

    def test_path_filters_do_not_make_generated_branch_push_safe(self):
        self.assert_blocked(
            workflow(
                """permissions: read-all
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                trigger="on:\n  push:\n    paths: ['trusted/**']",
            ),
            "references the secrets context",
        )

    def test_rejects_ambiguous_push_filter_forms(self):
        cases = {
            "must be a non-empty sequence": "on:\n  push:\n    branches: main",
            "dynamic or ambiguous": "on:\n  push:\n    branches: ['${{ inputs.branch }}']",
            "unsupported filter": "on:\n  push:\n    unknown: [main]",
            "cannot combine branches": (
                "on:\n  push:\n    branches: [main]\n    branches-ignore: ['codex/**']"
            ),
            "dynamic or empty event": "on: '${{ inputs.event }}'",
        }
        for diagnostic, trigger in cases.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(
                    workflow(
                        """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger=trigger,
                    ),
                    diagnostic,
                )



if __name__ == "__main__":
    unittest.main()
