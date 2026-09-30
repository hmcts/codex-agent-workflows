#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
RUNTIME = SCRIPTS.parents[1]
FAKE_NODE = (SCRIPTS / "test-codex-format-changed-files.py").read_text(encoding="utf-8")
FAKE_NODE = FAKE_NODE.split('FAKE_NODE = r"""', 1)[1].split('"""', 1)[0]

PIPELINE = """#!/usr/bin/env bash
set -euo pipefail
{
  echo "args=$*"
  echo "frontend_fast_command=${FRONTEND_FAST_COMMAND:-<unset>}"
  echo "app=$(cat src/app.ts)"
} >"${RUNNER_TEMP}/pipeline.log"
"""

WORKFLOW = """name: CI
on: push
permissions: {}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo test
"""


class VerifyFormattingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.runner_temp = self.root / "runner-temp"
        self.output_dir = self.runner_temp / "codex-output"
        self.output_dir.mkdir(parents=True)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        (fake_bin / "node").write_text(FAKE_NODE, encoding="utf-8")
        (fake_bin / "node").chmod(0o755)
        self.environment = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "CODEX_RUNTIME_PATH": str(RUNTIME),
            "OUTPUT_DIR": str(self.output_dir),
            "LOCAL_PIPELINE_MODE": "fast",
        }

        self.repository.mkdir()
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.write(".gitignore", "node_modules/\n")
        self.write(".yarnrc.yml", "yarnPath: .yarn/releases/yarn-4.10.3.cjs\n")
        self.write(".yarn/releases/yarn-4.10.3.cjs", "// pinned yarn release\n")
        self.write(".github/workflows/ci.yml", WORKFLOW)
        self.write("bin/codex-local-pipeline.sh", PIPELINE)
        (self.repository / "bin" / "codex-local-pipeline.sh").chmod(0o755)
        self.write("src/app.ts", "export const a = 1;\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base_sha = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/master", self.base_sha)

        self.write("src/app.ts", "export const a = 2;   \n")
        self.git("add", "-A")
        self.original_patch = self.git("diff", "--cached", "--binary", "HEAD")
        self.git("reset", "-q", "--hard", "HEAD")
        (self.output_dir / "changes.patch").write_text(self.original_patch, encoding="utf-8")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            env=self.environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def write(self, relative_path: str, content: str) -> None:
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def verification(self) -> dict[str, str]:
        lines = (self.output_dir / "verification.env").read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def run_script(self, script: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPTS / script)],
            cwd=self.repository,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
        )

    def run_jira_verify(self, **environment: str) -> subprocess.CompletedProcess[str]:
        (self.output_dir / "metadata.env").write_text("branch_name=codex/test\n", encoding="utf-8")
        (self.output_dir / "codex-pr-body.md").write_text("Body\n", encoding="utf-8")
        return self.run_script(
            "codex-jira-verify.sh",
            EXPECTED_BRANCH_NAME="codex/test",
            DEFAULT_BRANCH="master",
            **environment,
        )

    def run_review_verify(self, **environment: str) -> subprocess.CompletedProcess[str]:
        (self.output_dir / "metadata.env").write_text(
            "has_changes=true\npr_number=7\nhead_ref=codex/test\nbase_ref=master\n"
            f"head_sha={self.base_sha}\nbase_sha={self.base_sha}\n",
            encoding="utf-8",
        )
        (self.output_dir / "codex-review-comment.md").write_text("Comment\n", encoding="utf-8")
        return self.run_script(
            "codex-pr-review-verify.sh",
            EXPECTED_PR_NUMBER="7",
            EXPECTED_HEAD_REF="codex/test",
            EXPECTED_BASE_REF="master",
            EXPECTED_HEAD_SHA=self.base_sha,
            EXPECTED_BASE_SHA=self.base_sha,
            TRUSTED_PIPELINE_PATH=str(self.repository / "bin" / "codex-local-pipeline.sh"),
            TRUSTED_PR_SAFETY_PATH=str(SCRIPTS / "check-codex-pr-safety.rb"),
            TRUSTED_POLICY_PREPARER_PATH=str(SCRIPTS / "codex-prepare-policy-candidate.sh"),
            **environment,
        )

    def assert_formatted_and_recorded(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        patch = (self.output_dir / "changes.patch").read_bytes()
        self.assertIn(b"+export const a = 2;\n", patch)
        self.assertNotEqual(patch, self.original_patch.encode("utf-8"))
        self.assertEqual(self.verification()["patch_sha"], hashlib.sha256(patch).hexdigest())
        pipeline = (self.runner_temp / "pipeline.log").read_text(encoding="utf-8")
        self.assertIn("args=fast --base master --no-fetch", pipeline)
        self.assertIn("frontend_fast_command=yarn lint", pipeline)
        self.assertIn("app=export const a = 2;\n", pipeline + "\n")
        node_calls = (self.runner_temp / "node.log").read_text(encoding="utf-8")
        self.assertIn("prettier --write --ignore-unknown -- src/app.ts", node_calls)

    def assert_untouched_default(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        patch = (self.output_dir / "changes.patch").read_bytes()
        self.assertEqual(patch, self.original_patch.encode("utf-8"))
        self.assertEqual(self.verification()["patch_sha"], hashlib.sha256(patch).hexdigest())
        pipeline = (self.runner_temp / "pipeline.log").read_text(encoding="utf-8")
        self.assertIn("frontend_fast_command=<unset>", pipeline)
        self.assertFalse((self.runner_temp / "node.log").exists())

    def test_jira_verification_formats_then_verifies_the_formatted_patch(self):
        completed = self.run_jira_verify(
            CODEX_FORMATTER="prettier", CODEX_FRONTEND_FAST_COMMAND="yarn lint"
        )
        self.assert_formatted_and_recorded(completed)

    def test_jira_verification_is_unchanged_by_default(self):
        self.assert_untouched_default(self.run_jira_verify())

    def test_review_verification_formats_then_verifies_the_formatted_patch(self):
        completed = self.run_review_verify(
            CODEX_FORMATTER="prettier",
            CODEX_FRONTEND_FAST_COMMAND="yarn lint",
            TRUSTED_FORMATTER_PATH=str(SCRIPTS / "codex-format-changed-files.sh"),
        )
        self.assert_formatted_and_recorded(completed)

    def test_review_verification_is_unchanged_by_default(self):
        self.assert_untouched_default(self.run_review_verify())

    def test_review_verification_needs_the_trusted_formatter(self):
        completed = self.run_review_verify(CODEX_FORMATTER="prettier")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Missing trusted formatter", completed.stderr)

    def test_unknown_formatter_fails_verification(self):
        completed = self.run_jira_verify(CODEX_FORMATTER="black")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CODEX_FORMATTER must be none or prettier", completed.stderr)


if __name__ == "__main__":
    unittest.main()
