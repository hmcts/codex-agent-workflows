#!/usr/bin/env python3
"""Regression tests for terminal PR failure handling."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-mark-pr-failed.sh")
EXPECTED_SHA = "a" * 40


class MarkPrFailedTest(unittest.TestCase):
    def run_script(
        self,
        *,
        actual_sha: str = EXPECTED_SHA,
        draft: bool = False,
        notify_jira: bool = False,
    ):
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
                "title": "JS-123: Correct juror record",
                "body": (
                    "### Jira link\n\n"
                    "See [JS-123](https://tools.hmcts.net/jira/browse/JS-123)\n"
                ),
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

            jira_payloads: list[dict[str, object]] = []

            class JiraCallbackHandler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:  # noqa: N802
                    content_length = int(self.headers["Content-Length"])
                    jira_payloads.append(
                        json.loads(self.rfile.read(content_length).decode("utf-8"))
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

                def log_message(self, format: str, *args: object) -> None:
                    return

            callback_server = ThreadingHTTPServer(("127.0.0.1", 0), JiraCallbackHandler)
            callback_thread = threading.Thread(
                target=callback_server.serve_forever,
                daemon=True,
            )
            callback_thread.start()

            caller = root / "minimal-caller"
            caller.mkdir()
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GH_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "hmcts/juror-api",
                "GITHUB_RUN_ID": "12345",
                "PR_NUMBER": "42",
                "EXPECTED_HEAD_SHA": EXPECTED_SHA,
                "FAILURE_MESSAGE": "Required verification failed.",
                "FAILURE_DIR": str(failure_dir),
                "RUNNER_TEMP": str(root / "runner"),
            }
            if notify_jira:
                env.update(
                    {
                        "NOTIFY_JIRA": "true",
                        "CODEX_JIRA_PR_NOTIFY_URL": (
                            f"http://127.0.0.1:{callback_server.server_port}/callback"
                        ),
                    }
                )
            try:
                completed = subprocess.run(
                    ["bash", str(SCRIPT)],
                    cwd=caller,
                    env=env,
                    capture_output=True,
                    text=True,
                )
            finally:
                callback_server.shutdown()
                callback_server.server_close()
                callback_thread.join(timeout=2)
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            comment = comment_capture.read_text(encoding="utf-8") if comment_capture.exists() else ""
            return completed, commands, comment, jira_payloads

    def test_marks_expected_ready_pr_as_draft_and_comments_with_evidence(self):
        completed, commands, comment, _ = self.run_script()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("pr ready 42 --undo --repo hmcts/juror-api", commands)
        self.assertIn("pr comment 42 --repo hmcts/juror-api", commands)
        self.assertIn("returned to draft", comment)
        self.assertIn("Jenkins failed the required build.", comment)

    def test_does_not_repeat_ready_conversion_for_existing_draft(self):
        completed, commands, _, _ = self.run_script(draft=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("pr ready", commands)
        self.assertIn("pr comment 42 --repo hmcts/juror-api", commands)

    def test_rejects_moved_pr_head(self):
        completed, commands, _, _ = self.run_script(actual_sha="b" * 40)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("head revision moved", completed.stderr)
        self.assertNotIn("pr ready", commands)
        self.assertNotIn("pr comment", commands)

    def test_notifies_jira_from_generated_pr_metadata(self):
        completed, _, _, jira_payloads = self.run_script(notify_jira=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(jira_payloads), 1)
        self.assertEqual(jira_payloads[0]["issueKey"], "JS-123")
        self.assertEqual(
            jira_payloads[0]["issueUrl"],
            "https://tools.hmcts.net/jira/browse/JS-123",
        )
        self.assertEqual(jira_payloads[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
