#!/usr/bin/env python3
"""A /codex-review run where Codex makes no change must post Codex's comment.

Collection writes no patch for a no-change result, so review publication must
skip the patch steps. Before this was fixed, the policy preparer failed on the
missing patch and the run ended red without a comment."""

from __future__ import annotations

import json
import re

from publisher_test_support import *  # noqa: F403


WORKFLOW = SCRIPT_DIR.parent / "workflows" / "codex-review-publish.yml"
COLLECTOR = SCRIPT_DIR / "codex-pr-review-collect.sh"
PREPARER = SCRIPT_DIR / "codex-prepare-policy-candidate.sh"
NO_CHANGE_RESULT = json.dumps(
    {
        "has_changes": False,
        "patch_gzip_base64": "",
        "summary": "The feedback needs no code change.",
        "testing": "Not applicable.",
    }
)


def publish_job() -> str:
    content = WORKFLOW.read_text(encoding="utf-8")
    start = content.index("\n  codex-review-publish:\n")
    end = content.index("\n  verify-review-status:\n")
    return content[start:end]


def step(job: str, name: str) -> str:
    start = job.index(f"      - name: {name}\n")
    following = job.find("\n      - name: ", start + 1)
    return job[start : following if following != -1 else len(job)]


def step_script(job: str, name: str) -> str:
    block = step(job, name)
    body = block[block.index("        run: |\n") + len("        run: |\n") :]
    return "".join(line[10:] + "\n" for line in body.splitlines() if line.startswith("          "))


class NoChangeReviewPublicationTests(PublisherTestCase):
    def collect_no_change(self, root: Path) -> Path:
        output = root / "codex-output"
        completed = subprocess.run(
            ["bash", str(COLLECTOR)],
            env={
                **os.environ,
                "CODEX_SHOULD_RUN": "true",
                "CODEX_RESULT": NO_CHANGE_RESULT,
                "OUTPUT_DIR": str(output),
                "PR_NUMBER": "42",
                "HEAD_REF": "codex/example",
                "BASE_REF": "master",
                "HEAD_SHA": HEAD_SHA,
                "BASE_SHA": BASE_SHA,
                "COMMENT_AUTHOR": "reviewer",
                "COMMENT_URL": "https://example.invalid/comment/1",
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return output

    def test_collection_records_a_no_change_result_without_a_patch(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = self.collect_no_change(Path(temporary_directory))
            self.assertIn("has_changes=false\n", (output / "metadata.env").read_text(encoding="utf-8"))
            self.assertIn(
                "did not produce any committable changes",
                (output / "codex-review-comment.md").read_text(encoding="utf-8"),
            )
            self.assertFalse((output / "changes.patch").exists())

    def test_policy_preparer_refuses_a_missing_patch(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = self.collect_no_change(root)
            repository = root / "candidate"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "base"],
                check=True,
            )
            head = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            completed = subprocess.run(
                ["bash", str(PREPARER)],
                env={
                    **os.environ,
                    "CANDIDATE_ROOT": str(repository),
                    "EXPECTED_CANDIDATE_SHA": head,
                    "PATCH_PATH": str(output / "changes.patch"),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("Candidate policy patch is missing", completed.stderr)

    def test_result_step_reads_the_trusted_result_strictly(self):
        script = step_script(publish_job(), "Read the review result")
        for metadata, expected in (
            ("has_changes=true\npr_number=42\n", "true"),
            ("pr_number=42\nhas_changes=false\n", "false"),
            ("has_changes=maybe\n", None),
            ("pr_number=42\n", None),
        ):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                (root / "codex-output").mkdir()
                (root / "codex-output" / "metadata.env").write_text(metadata, encoding="utf-8")
                github_output = root / "github-output"
                completed = subprocess.run(
                    ["bash", "-e", "-c", script],
                    env={**os.environ, "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(github_output)},
                    capture_output=True,
                    text=True,
                )
                if expected is None:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("Unreadable review result", completed.stdout)
                else:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(github_output.read_text(encoding="utf-8"), f"has_changes={expected}\n")

    def test_publication_skips_the_patch_steps_but_still_gates_before_the_token(self):
        job = publish_job()
        condition = "steps.review-result.outputs.has_changes == 'true'"
        download = job.index("      - name: Download Codex review output\n")
        result = job.index("      - name: Read the review result\n")
        materialize = job.index("      - name: Materialize candidate policy tree\n")
        gate = job.index("      - name: Enforce pull-request credential isolation\n")
        token = job.index("      - name: Create GitHub App installation token\n")
        self.assertLess(download, result)
        self.assertLess(result, materialize)
        self.assertLess(materialize, gate)
        self.assertLess(gate, token)
        for name in (
            "Materialize candidate policy tree",
            "Download formatted Codex review patch",
            "Adopt formatted Codex review patch",
        ):
            with self.subTest(step=name):
                self.assertRegex(step(job, name), rf"(?m)^        if: .*{re.escape(condition)}$")
        self.assertNotIn("        if:", step(job, "Enforce pull-request credential isolation"))
        self.assertNotIn("        if:", step(job, "Publish Codex review feedback"))

    def test_publisher_posts_the_comment_and_pushes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = self.collect_no_change(root)
            verified = root / "verified"
            verified.mkdir()
            (verified / "verification.env").write_text(
                "has_changes=false\npr_number=42\nhead_ref=codex/example\nbase_ref=master\n"
                f"head_sha={HEAD_SHA}\nbase_sha={BASE_SHA}\n",
                encoding="utf-8",
            )
            comment = (output / "codex-review-comment.md").read_text(encoding="utf-8")
            (verified / "codex-review-comment.md").write_text(comment, encoding="utf-8")
            fake_bin, command_log = self.make_fake_tools(root, remote_base=BASE_SHA, remote_head=HEAD_SHA)
            github_output = root / "github-output"
            completed = subprocess.run(
                ["bash", str(REVIEW_PUBLISHER)],
                cwd=SCRIPT_DIR.parent.parent,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "GH_TOKEN": "test-token",
                    "BOT_PUBLISHER_LOGIN": "appreg-codex-bot",
                    "BOT_PUBLISHER_EMAIL": "12345+appreg-codex-bot[bot]@users.noreply.github.com",
                    "GITHUB_REPOSITORY": "hmcts/example",
                    "OUTPUT_DIR": str(output),
                    "VERIFICATION_DIR": str(verified),
                    "EXPECTED_PR_NUMBER": "42",
                    "EXPECTED_HEAD_REF": "codex/example",
                    "EXPECTED_HEAD_SHA": HEAD_SHA,
                    "DEFAULT_BRANCH": "master",
                    "EXPECTED_DEFAULT_SHA": BASE_SHA,
                    "RUNNER_TEMP": str(root / "runner"),
                    "GITHUB_OUTPUT": str(github_output),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Codex produced no review-feedback changes for PR #42.", completed.stdout)
            self.assertEqual((root / "published-comment.md").read_text(encoding="utf-8"), comment)
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            self.assertNotIn("push", commands)
            outputs = github_output.read_text(encoding="utf-8")
            self.assertIn("pr_number=42\n", outputs)
            self.assertNotIn("commit_sha=", outputs)


if __name__ == "__main__":
    unittest.main()
