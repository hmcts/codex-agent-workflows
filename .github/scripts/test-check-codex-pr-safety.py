#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-codex-pr-safety.py")


class CheckCodexPrSafetyTests(unittest.TestCase):
    def run_check(self, workflows: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            for name, content in workflows.items():
                (workflow_dir / name).write_text(content, encoding="utf-8")
            return subprocess.run(
                ["python3", "-I", str(SCRIPT), "--repository-root", str(root)],
                capture_output=True,
                text=True,
            )

    def test_rejects_scheduler_style_pr_oidc_workflow_with_clear_diagnostic(self):
        completed = self.run_check(
            {
                "ci.yml": """on:\n  pull_request:\n    branches: [master]\npermissions:\n  contents: read\n  id-token: write\njobs:\n  build:\n    steps:\n      - run: ./gradlew check\n"""
            }
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Unsafe PR credential exposure", completed.stderr)
        self.assertIn(".github/workflows/ci.yml", completed.stderr)

    def test_accepts_push_only_oidc_workflow(self):
        completed = self.run_check(
            {
                "publish.yml": """on:\n  push:\n    branches: [master]\npermissions:\n  id-token: write\n"""
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_accepts_pr_workflow_without_oidc_write(self):
        completed = self.run_check(
            {"ci.yaml": "on: [pull_request]\npermissions:\n  contents: read\n"}
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_flow_pr_trigger_with_write_all(self):
        completed = self.run_check(
            {"ci.yml": "'on': [push, pull_request_target]\npermissions: write-all\n"}
        )

        self.assertEqual(completed.returncode, 1)

    def test_rejects_flow_permissions_with_id_token_write(self):
        completed = self.run_check(
            {
                "ci.yml": (
                    "on: pull_request\n"
                    "jobs:\n"
                    "  test:\n"
                    "    permissions: {contents: read, id-token: write}\n"
                )
            }
        )

        self.assertEqual(completed.returncode, 1)


if __name__ == "__main__":
    unittest.main()
