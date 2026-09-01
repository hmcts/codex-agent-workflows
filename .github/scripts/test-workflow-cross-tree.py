#!/usr/bin/env python3

from workflow_safety_test_support import *  # noqa: F403


class WorkflowCrossTreeTests(WorkflowSafetyTestCase):
    def test_cross_tree_candidate_cannot_hide_unchanged_trusted_listener(self):
        trusted_build = named_workflow(
            "Build",
            """permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps: []""",
            trigger="on:\n  push:\n    branches: [main]",
        )
        candidate_build = trusted_build.replace(
            "on:\n  push:\n    branches: [main]", "on: pull_request"
        )
        trusted_listener = named_workflow(
            "Privileged Listener",
            """permissions:
  contents: write
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
            trigger=(
                "on:\n  workflow_run:\n"
                "    workflows: [Build]\n"
                "    types: [completed]"
            ),
        )

        for case, candidate in {
            "deleted listener": {"build.yml": candidate_build},
            "weakened candidate copy": {
                "build.yml": candidate_build,
                "listener.yml": trusted_listener.replace(
                    "contents: write", "contents: read"
                ),
            },
        }.items():
            with self.subTest(case=case):
                self.assert_workflows_blocked(
                    candidate,
                    "effective write permission(s): contents",
                    filename="listener.yml",
                    trusted_workflows={
                        "build.yml": trusted_build,
                        "listener.yml": trusted_listener,
                    },
                )

    def test_cross_tree_candidate_added_upstream_reaches_trusted_listener(self):
        candidate = {
            "generated.yml": named_workflow(
                "Candidate Build",
                """permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on:\n  push:\n    branches: ['codex/**']",
            )
        }
        trusted = {
            "listener.yml": named_workflow(
                "Default Listener",
                """permissions:
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps: []""",
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [Candidate Build]\n"
                    "    types: [requested]"
                ),
            )
        }
        self.assert_workflows_blocked(
            candidate,
            "effective write permission(s): id-token",
            filename="listener.yml",
            trusted_workflows=trusted,
        )

    def test_cross_tree_workflow_names_and_first_hop_filters_are_resolved(self):
        candidate = {
            "generated.yml": named_workflow(
                "Candidate Build",
                """permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on:\n  push:\n    branches: ['codex/**']",
            )
        }
        listener_body = """permissions: write-all
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []"""
        trusted_main_filter = {
            "listener.yml": named_workflow(
                "Default Listener",
                listener_body,
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [Candidate Build]\n"
                    "    types: [completed]\n"
                    "    branches: [main]"
                ),
            )
        }
        completed = self.run_check(
            candidate,
            trusted_workflows=trusted_main_filter,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        trusted_codex_filter = {
            "listener.yml": trusted_main_filter["listener.yml"].replace(
                "branches: [main]", "branches: ['codex/**']"
            )
        }
        self.assert_workflows_blocked(
            candidate,
            "effective write permission",
            filename="listener.yml",
            trusted_workflows=trusted_codex_filter,
        )

        trusted_wrong_name = {
            "listener.yml": trusted_main_filter["listener.yml"].replace(
                "workflows: [Candidate Build]", "workflows: [Renamed Build]"
            )
        }
        self.assert_workflows_blocked(
            candidate,
            "missing candidate/trusted upstream workflow",
            filename="listener.yml",
            trusted_workflows=trusted_wrong_name,
        )

    def test_cross_tree_nested_hop_cannot_restore_trust_with_default_branch_filter(self):
        candidate = {
            "generated.yml": named_workflow(
                "Candidate Build",
                """permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on: pull_request",
            )
        }
        trusted = {
            "first.yml": named_workflow(
                "First Listener",
                """permissions: read-all
jobs:
  first:
    runs-on: ubuntu-latest
    steps: []""",
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [Candidate Build]\n"
                    "    types: [completed]"
                ),
            ),
            "second.yml": named_workflow(
                "Second Listener",
                """permissions:
  deployments: write
jobs:
  second:
    runs-on: ubuntu-latest
    steps: []""",
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [First Listener]\n"
                    "    types: [completed]\n"
                    "    branches: [main]"
                ),
            ),
        }
        self.assert_workflows_blocked(
            candidate,
            "effective write permission(s): deployments",
            filename="second.yml",
            trusted_workflows=trusted,
        )

    def test_cross_tree_second_hop_cannot_filter_first_listener_branch(self):
        candidate = {
            "generated.yml": named_workflow(
                "Candidate Build",
                """permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on:\n  push:\n    branches: ['codex/**']",
            )
        }
        trusted = {
            "first.yml": named_workflow(
                "First Listener",
                """permissions: read-all
jobs:
  first:
    runs-on: ubuntu-latest
    steps: []""",
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [Candidate Build]\n"
                    "    types: [completed]\n"
                    "    branches: ['codex/**']"
                ),
            ),
            "second.yml": named_workflow(
                "Second Listener",
                """permissions:
  id-token: write
jobs:
  second:
    runs-on: ubuntu-latest
    steps: []""",
                trigger=(
                    "on:\n  workflow_run:\n"
                    "    workflows: [First Listener]\n"
                    "    types: [completed]\n"
                    "    branches: [main]"
                ),
            ),
        }
        self.assert_workflows_blocked(
            candidate,
            "effective write permission(s): id-token",
            filename="second.yml",
            trusted_workflows=trusted,
        )

    def test_checker_requires_an_available_valid_trusted_tree(self):
        safe = workflow(
            """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "ci.yml").write_text(safe, encoding="utf-8")

            missing_argument = subprocess.run(
                ["ruby", "--disable-gems", str(SCRIPT), "--repository-root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing_argument.returncode, 2)
            self.assertIn("--trusted-repository-root", missing_argument.stderr)

            missing_tree = subprocess.run(
                [
                    "ruby",
                    "--disable-gems",
                    str(SCRIPT),
                    "--repository-root",
                    str(root),
                    "--trusted-repository-root",
                    str(root / "missing"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing_tree.returncode, 1)
            self.assertIn("trusted:.github/workflows", missing_tree.stderr)

            malformed_root = root / "malformed"
            malformed_workflows = malformed_root / ".github" / "workflows"
            malformed_workflows.mkdir(parents=True)
            (malformed_workflows / "ci.yml").write_text(
                "on: [pull_request\n", encoding="utf-8"
            )
            malformed_tree = subprocess.run(
                [
                    "ruby",
                    "--disable-gems",
                    str(SCRIPT),
                    "--repository-root",
                    str(root),
                    "--trusted-repository-root",
                    str(malformed_root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(malformed_tree.returncode, 1)
            self.assertIn("trusted:.github/workflows/ci.yml: malformed YAML", malformed_tree.stderr)
            self.assertNotIn("graph analysis failure", malformed_tree.stderr)



if __name__ == "__main__":
    unittest.main()
