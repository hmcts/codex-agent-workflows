#!/usr/bin/env python3
"""Regression tests for terminal PR failure handling."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-mark-pr-failed.sh")
EXPECTED_SHA = "a" * 40


class MarkPrFailedTest(unittest.TestCase):
    def run_script(self, *, actual_sha: str = EXPECTED_SHA, draft: bool = False):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            command_log = root / "commands.log"
            comment_capture = root / "comment.md"
            failure_dir = root / "failure"
            failure_dir.mkdir()
            (failure_dir / "verification-failure-summary.log").write_text(
                "Jenkins failed the required build.\n", encoding="utf-8"
            )

            pull_request = {
                "state": "open",
                "draft": draft,
                "head": {
                    "sha": actual_sha,
                    "repo": {"full_name": "hmcts/juror-api"},
                },
            }
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"printf '%s\\n' \"$*\" >>{str(command_log)!r}\n"
                "if [[ \"$1\" == \"api\" ]]; then\n"
                f"  printf '%s\\n' {shlex.quote(json.dumps(pull_request))}\n"
                "elif [[ \"$1 $2\" == \"pr comment\" ]]; then\n"
                "  while [[ $# -gt 0 ]]; do\n"
                "    if [[ \"$1\" == \"--body-file\" ]]; then\n"
                f"      cp \"$2\" {str(comment_capture)!r}\n"
                "      break\n"
                "    fi\n"
                "    shift\n"
                "  done\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            caller = root / "minimal-caller"
            caller.mkdir()
            completed = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=caller,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "GH_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "hmcts/juror-api",
                    "PR_NUMBER": "42",
                    "EXPECTED_HEAD_SHA": EXPECTED_SHA,
                    "FAILURE_MESSAGE": "Required verification failed.",
                    "FAILURE_DIR": str(failure_dir),
                    "RUNNER_TEMP": str(root / "runner"),
                },
                capture_output=True,
                text=True,
            )
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            comment = comment_capture.read_text(encoding="utf-8") if comment_capture.exists() else ""
            return completed, commands, comment

    def test_marks_expected_ready_pr_as_draft_and_comments_with_evidence(self):
        completed, commands, comment = self.run_script()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("pr ready 42 --undo --repo hmcts/juror-api", commands)
        self.assertIn("pr comment 42 --repo hmcts/juror-api", commands)
        self.assertIn("returned to draft", comment)
        self.assertIn("Jenkins failed the required build.", comment)

    def test_does_not_repeat_ready_conversion_for_existing_draft(self):
        completed, commands, _ = self.run_script(draft=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("pr ready", commands)
        self.assertIn("pr comment 42 --repo hmcts/juror-api", commands)

    def test_rejects_moved_pr_head(self):
        completed, commands, _ = self.run_script(actual_sha="b" * 40)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("head revision moved", completed.stderr)
        self.assertNotIn("pr ready", commands)
        self.assertNotIn("pr comment", commands)


if __name__ == "__main__":
    unittest.main()
