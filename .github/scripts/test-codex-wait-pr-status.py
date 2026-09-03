#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-wait-pr-status.sh")
CONTEXT = "continuous-integration/jenkins/pr-head"


class WaitForPrStatusTests(unittest.TestCase):
    def run_status(self, state: str, description: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            gh = root / "gh"
            response = {
                "statuses": [
                    {
                        "context": CONTEXT,
                        "state": state,
                        "description": description,
                        "target_url": "https://jenkins.example/job/1",
                    }
                ]
            }
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "cat <<'JSON'\n"
                f"{json.dumps(response)}\n"
                "JSON\n",
                encoding="utf-8",
            )
            gh.chmod(0o700)

            return subprocess.run(
                [str(SCRIPT)],
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "GH_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "hmcts/example",
                    "PUBLISHED_COMMIT_SHA": "a" * 40,
                    "PR_NUMBER": "123",
                    "REQUIRED_STATUS_TIMEOUT_SECONDS": "0",
                    "REQUIRED_STATUS_POLL_SECONDS": "0",
                },
                capture_output=True,
                text=True,
            )

    def test_successful_required_status_passes(self):
        completed = self.run_status("success", "Build completed")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Required status passed", completed.stdout)

    def test_unbuildable_status_has_distinct_exit_code(self):
        completed = self.run_status("error", "This commit cannot be built")

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("not buildable", completed.stdout)

    def test_real_jenkins_failure_remains_repairable(self):
        completed = self.run_status("failure", "Tests failed")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Required status failed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
