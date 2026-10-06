#!/usr/bin/env python3
"""Shared workflow steps must run the release's own scripts, and report what they find.

In a reusable workflow, actions/checkout without a repository fetches the
caller's repository, which has none of these scripts. The runner-target check
did exactly that, so every caller run failed in its first job."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOWS = SCRIPT_DIR.parent / "workflows"
SHARED_WORKFLOWS = sorted(WORKFLOWS.glob("codex-*.yml"))
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\n", re.M)


def jobs(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    body = text[text.index("\njobs:\n") :]
    headers = list(JOB_HEADER.finditer(body))
    return {
        match.group(1): body[match.start() : headers[index + 1].start() if index + 1 < len(headers) else len(body)]
        for index, match in enumerate(headers)
    }


def step_script(job: str, name: str) -> str:
    start = job.index(f"      - name: {name}\n")
    following = job.find("\n      - name: ", start + 1)
    block = job[start : following if following != -1 else len(job)]
    body = block[block.index("        run: |\n") + len("        run: |\n") :]
    return "".join(line[10:] + "\n" for line in body.splitlines() if line.startswith("          "))


class SharedRuntimeScriptTests(unittest.TestCase):
    def test_shared_scripts_are_run_from_the_release_runtime(self):
        for path in SHARED_WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\.github/scripts/", text):
                with self.subTest(workflow=path.name, offset=match.start()):
                    prefix = text[max(0, match.start() - 20) : match.start()]
                    self.assertRegex(prefix, r"CODEX_RUNTIME_PATH\}?/$")

    def test_jobs_that_use_the_runtime_locate_it_first(self):
        for path in SHARED_WORKFLOWS:
            for name, job in jobs(path).items():
                if "CODEX_RUNTIME_PATH" not in job:
                    continue
                with self.subTest(workflow=path.name, job=name):
                    locate = job.find("uses: $/.github/actions/runtime")
                    self.assertNotEqual(locate, -1)
                    self.assertLess(locate, job.index("CODEX_RUNTIME_PATH"))

    def test_runner_target_check_runs_from_the_runtime(self):
        for name in ("codex-implement.yml", "codex-review-feedback.yml"):
            with self.subTest(workflow=name):
                job = jobs(WORKFLOWS / name)["validate-runner-target"]
                self.assertNotIn("actions/checkout", job)
                self.assertIn('"${CODEX_RUNTIME_PATH}/.github/scripts/validate-runner-target.py"', job)


class RepairedStatusStepTests(unittest.TestCase):
    STEP = "Check repaired external PR status"

    def run_step(self, wait_exit_code: int) -> tuple[subprocess.CompletedProcess[str], str, Path]:
        job = next(job for job in jobs(WORKFLOWS / "codex-post-repair.yml").values() if self.STEP in job)
        script = step_script(job, self.STEP)
        root = Path(tempfile.mkdtemp())
        runtime_scripts = root / "runtime" / ".github" / "scripts"
        runtime_scripts.mkdir(parents=True)
        wait = runtime_scripts / "codex-wait-pr-status.sh"
        wait.write_text(f"#!/usr/bin/env bash\necho 'Waiting for required PR status'\nexit {wait_exit_code}\n", encoding="utf-8")
        wait.chmod(0o755)
        (root / "runner").mkdir()
        output = root / "github-output"
        completed = subprocess.run(
            ["bash", "-e", "-c", script],
            env={
                **os.environ,
                "CODEX_RUNTIME_PATH": str(root / "runtime"),
                "RUNNER_TEMP": str(root / "runner"),
                "GITHUB_OUTPUT": str(output),
                "LOCAL_VERIFICATION_PASSED": "true",
                "LOCAL_FAILURE_ARTIFACT": "codex-local-failure",
                "STATUS_FAILURE_ARTIFACT": "codex-jira-pr-status-failure-1",
            },
            capture_output=True,
            text=True,
        )
        return completed, output.read_text(encoding="utf-8") if output.exists() else "", root

    def test_a_passing_status_marks_the_repair_passed(self):
        completed, outputs, _ = self.run_step(0)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("passed=true\n", outputs)
        self.assertIn("Waiting for required PR status", completed.stdout)

    def test_a_failing_status_keeps_the_evidence(self):
        completed, outputs, root = self.run_step(1)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("passed=false\n", outputs)
        self.assertIn("failure_artifact=codex-jira-pr-status-failure-1\n", outputs)
        evidence = root / "runner" / "codex-repaired-pr-status-failure" / "verification-failure.log"
        self.assertIn("Waiting for required PR status", evidence.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
