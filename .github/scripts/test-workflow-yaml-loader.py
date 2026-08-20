#!/usr/bin/env python3

from workflow_safety_test_support import *  # noqa: F403


class WorkflowYamlLoaderTests(WorkflowSafetyTestCase):
    def test_accepts_normal_read_only_pr_workflow(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./gradlew check"""
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_accepts_sequence_trigger_with_explicit_job_permissions(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """jobs:
  test:
    permissions: {}
    runs-on: ubuntu-latest
    steps:
      - run: make test""",
                    trigger="on:\n  - push\n  - pull_request",
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_folded_and_literal_write_permissions(self):
        for style in (">-", "|"):
            with self.subTest(style=style):
                self.assert_blocked(
                    workflow(
                        f"""permissions:
  id-token: {style}
    write
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger="on:\n  - pull_request",
                    ),
                    "effective write permission(s): id-token",
                )

    def test_resolves_trigger_and_permission_aliases(self):
        completed = self.run_check(
            {
                "ci.yml": """x-events: &events [push, pull_request]
on: *events
permissions: &read-only
  contents: read
jobs:
  test:
    permissions: *read-only
    runs-on: ubuntu-latest
    steps: []
"""
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_alias_cannot_hide_write_permission(self):
        self.assert_blocked(
            """on: [pull_request]
permissions: &cloud
  id-token: write
jobs:
  test:
    permissions: *cloud
    runs-on: ubuntu-latest
    steps: []
""",
            "effective write permission(s): id-token",
        )

    def test_rejects_malformed_duplicate_merge_and_unresolved_alias_yaml(self):
        cases = {
            "malformed YAML": "on: [pull_request\npermissions: read-all\n",
            "duplicate key": """on: pull_request
permissions: read-all
permissions: {}
jobs: {}
""",
            "YAML merge key": """on: pull_request
permissions: &base
  contents: read
jobs:
  test:
    <<: *base
    permissions: {}
""",
            "YAML could not be resolved safely": """on: *missing
permissions: read-all
jobs: {}
""",
            "exactly one YAML document": """on: push
---
on: pull_request
permissions: read-all
jobs: {}
""",
        }
        for diagnostic, content in cases.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(content, diagnostic)

    def test_rejects_ambiguous_top_level_boolean_key(self):
        for ambiguous_key in ("true", "ON", "yes"):
            with self.subTest(ambiguous_key=ambiguous_key):
                self.assert_blocked(
                    f"""on: pull_request
{ambiguous_key}: push
permissions: read-all
jobs: {{}}
""",
                    "special on semantics",
                )

    def test_inspects_dot_prefixed_workflow_files(self):
        self.assert_blocked(
            workflow(
                """permissions: write-all
jobs:
  hidden:
    runs-on: ubuntu-latest
    steps: []"""
            ),
            "effective write permission",
            filename=".hidden.yml",
        )

    def test_rejects_uninspectable_workflow_directory_entries(self):
        safe = workflow(
            """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
        )
        cases = (
            ({"ci.yml": safe, "notes.txt": "not a workflow\n"}, (), None, "notes.txt", "unsupported extension"),
            ({"ci.yml": safe}, ("nested",), None, "nested", "regular file"),
            ({"ci.yml": safe}, (), {"linked.yml": "ci.yml"}, "linked.yml", "symbolic link"),
        )
        for workflows, directories, symlinks, filename, diagnostic in cases:
            with self.subTest(filename=filename):
                self.assert_workflows_blocked(
                    workflows,
                    diagnostic,
                    filename=filename,
                    directories=directories,
                    symlinks=symlinks,
                )



if __name__ == "__main__":
    unittest.main()
