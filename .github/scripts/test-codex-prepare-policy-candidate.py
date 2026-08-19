#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-prepare-policy-candidate.sh")


class PreparePolicyCandidateTests(unittest.TestCase):
    def git(self, repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def test_materializes_trusted_snapshot_before_applying_candidate_patch(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "candidate"
            workflow_path = repository / ".github" / "workflows" / "ci.yml"
            workflow_path.parent.mkdir(parents=True)
            self.git(repository.parent, "init", str(repository))
            self.git(repository, "config", "user.name", "Test User")
            self.git(repository, "config", "user.email", "test@example.com")

            workflow_path.write_text("name: Trusted\non: workflow_dispatch\n", encoding="utf-8")
            self.git(repository, "add", ".github/workflows/ci.yml")
            self.git(repository, "commit", "-m", "trusted")
            trusted_sha = self.git(repository, "rev-parse", "HEAD")

            workflow_path.write_text("name: Candidate\non: workflow_dispatch\n", encoding="utf-8")
            self.git(repository, "commit", "-am", "candidate")
            candidate_sha = self.git(repository, "rev-parse", "HEAD")

            workflow_path.write_text("name: Patched\non: pull_request\n", encoding="utf-8")
            patch_path = root / "changes.patch"
            patch_path.write_text(
                subprocess.run(
                    ["git", "-C", str(repository), "diff", "--binary"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                encoding="utf-8",
            )
            self.git(repository, "restore", ".github/workflows/ci.yml")

            trusted_root = root / "trusted"
            completed = subprocess.run(
                [str(SCRIPT)],
                env={
                    **os.environ,
                    "CANDIDATE_ROOT": str(repository),
                    "EXPECTED_CANDIDATE_SHA": candidate_sha,
                    "PATCH_PATH": str(patch_path),
                    "TRUSTED_REPOSITORY_ROOT": str(trusted_root),
                    "EXPECTED_TRUSTED_SHA": trusted_sha,
                },
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                workflow_path.read_text(encoding="utf-8"),
                "name: Patched\non: pull_request\n",
            )
            self.assertEqual(
                (trusted_root / ".github" / "workflows" / "ci.yml").read_text(
                    encoding="utf-8"
                ),
                "name: Trusted\non: workflow_dispatch\n",
            )
            self.assertEqual(
                self.git(repository, "diff", "--cached", "--name-only"),
                ".github/workflows/ci.yml",
            )

    def test_unavailable_trusted_snapshot_stops_before_patch_application(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "candidate"
            workflow_path = repository / ".github" / "workflows" / "ci.yml"
            workflow_path.parent.mkdir(parents=True)
            self.git(repository.parent, "init", str(repository))
            self.git(repository, "config", "user.name", "Test User")
            self.git(repository, "config", "user.email", "test@example.com")
            workflow_path.write_text("name: Candidate\non: workflow_dispatch\n", encoding="utf-8")
            self.git(repository, "add", ".github/workflows/ci.yml")
            self.git(repository, "commit", "-m", "candidate")
            candidate_sha = self.git(repository, "rev-parse", "HEAD")

            workflow_path.write_text("name: Patched\non: pull_request\n", encoding="utf-8")
            patch_path = root / "changes.patch"
            patch_path.write_text(
                subprocess.run(
                    ["git", "-C", str(repository), "diff", "--binary"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                encoding="utf-8",
            )
            self.git(repository, "restore", ".github/workflows/ci.yml")

            completed = subprocess.run(
                [str(SCRIPT)],
                env={
                    **os.environ,
                    "CANDIDATE_ROOT": str(repository),
                    "EXPECTED_CANDIDATE_SHA": candidate_sha,
                    "PATCH_PATH": str(patch_path),
                    "TRUSTED_REPOSITORY_ROOT": str(root / "trusted"),
                    "EXPECTED_TRUSTED_SHA": "f" * 40,
                },
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Immutable trusted commit is unavailable", completed.stderr)
            self.assertEqual(
                workflow_path.read_text(encoding="utf-8"),
                "name: Candidate\non: workflow_dispatch\n",
            )
            self.assertEqual(self.git(repository, "diff", "--cached", "--name-only"), "")


if __name__ == "__main__":
    unittest.main()
