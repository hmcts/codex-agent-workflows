#!/usr/bin/env python3

from publisher_test_constants import *  # noqa: F403


class PublisherExecutionHarnessMixin:
    @staticmethod
    def write_patch_artifacts(root: Path, *, kind: str) -> tuple[Path, Path]:
        output = root / "output"
        verified = root / "verified"
        output.mkdir()
        verified.mkdir()
        patch = b"not-a-real-patch-but-git-is-stubbed\n"
        patch_sha = hashlib.sha256(patch).hexdigest()

        if kind == "jira":
            (output / "changes.patch").write_bytes(patch)
            (verified / "changes.patch").write_bytes(patch)
            (output / "metadata.env").write_text(
                "branch_name=codex/example\n",
                encoding="utf-8",
            )
            (verified / "verification.env").write_text(
                "branch_name=codex/example\n"
                f"base_sha={BASE_SHA}\n"
                f"patch_sha={patch_sha}\n",
                encoding="utf-8",
            )
            (verified / "codex-pr-body.md").write_text("PR body", encoding="utf-8")
        elif kind == "review":
            (output / "changes.patch").write_bytes(patch)
            (verified / "changes.patch").write_bytes(patch)
            (output / "metadata.env").write_text(
                "has_changes=true\n"
                "pr_number=42\n"
                "head_ref=codex/example\n"
                "base_ref=master\n"
                f"head_sha={HEAD_SHA}\n"
                f"base_sha={BASE_SHA}\n"
                "comment_author=reviewer\n"
                "comment_url=https://example.invalid/comment/1\n",
                encoding="utf-8",
            )
            (verified / "verification.env").write_text(
                "has_changes=true\n"
                "pr_number=42\n"
                "head_ref=codex/example\n"
                "base_ref=master\n"
                f"head_sha={HEAD_SHA}\n"
                f"base_sha={BASE_SHA}\n"
                f"patch_sha={patch_sha}\n",
                encoding="utf-8",
            )
            (output / "codex-final-message.md").write_text(
                "Updated the code.",
                encoding="utf-8",
            )
            (verified / "codex-review-comment.md").write_text(
                "Manual verification required: sensitive workflow files changed.\n",
                encoding="utf-8",
            )
        else:
            raise ValueError(f"Unsupported artifact kind: {kind}")

        return output, verified

    def run_review_with_outputs(
        self,
        remote_head: str,
        *,
        remote_base: str = BASE_SHA,
        fail_pr_comment: bool = False,
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
    ) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, verified = self.write_patch_artifacts(root, kind="review")
            fake_bin, command_log = self.make_fake_tools(
                root,
                remote_base=remote_base,
                remote_head=remote_head,
                fail_pr_comment=fail_pr_comment,
                remote_parent=remote_parent,
                remote_tree=remote_tree,
            )
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
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            outputs = github_output.read_text(encoding="utf-8") if github_output.exists() else ""
            comment_path = root / "published-comment.md"
            comment = comment_path.read_text(encoding="utf-8") if comment_path.exists() else ""
            return completed, commands, outputs, comment

    def run_review(self, remote_head: str) -> tuple[subprocess.CompletedProcess[str], str]:
        completed, commands, _, _ = self.run_review_with_outputs(remote_head)
        return completed, commands

    def run_jira_with_outputs(
        self,
        *,
        mode: str,
        remote_base: str | Sequence[str] = BASE_SHA,
        remote_head: str | Sequence[str] = "",
        fail_notify: bool = False,
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
        pr_exists: bool = True,
        fail_pr_create: bool = False,
        pr_head_sha: str = NEW_SHA,
        pr_base_ref: str = "master",
        pr_base_sha: object = BASE_SHA,
        pr_base_repository: str = "hmcts/example",
        pr_head_ref: str = "codex/example",
        pr_head_repository: str = "hmcts/example",
        pr_is_draft: bool = False,
        multiple_prs: bool = False,
        expected_draft: bool = False,
        post_push_head: str = NEW_SHA,
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            caller = root / "minimal-caller"
            caller.mkdir()
            output, verified = self.write_patch_artifacts(root, kind="jira")
            fake_bin, command_log = self.make_fake_tools(
                root,
                remote_base=remote_base,
                remote_head=remote_head,
                remote_parent=remote_parent,
                remote_tree=remote_tree,
                pr_exists=pr_exists,
                fail_pr_create=fail_pr_create,
                pr_head_sha=pr_head_sha,
                pr_base_ref=pr_base_ref,
                pr_base_sha=pr_base_sha,
                pr_base_repository=pr_base_repository,
                pr_head_ref=pr_head_ref,
                pr_head_repository=pr_head_repository,
                pr_is_draft=pr_is_draft,
                multiple_prs=multiple_prs,
                post_push_head=post_push_head,
            )
            github_output = root / "github-output"
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GH_TOKEN": "test-token",
                "BOT_PUBLISHER_LOGIN": "appreg-codex-bot",
                "BOT_PUBLISHER_EMAIL": "12345+appreg-codex-bot[bot]@users.noreply.github.com",
                "GITHUB_REPOSITORY": "hmcts/example",
                "GITHUB_ACTOR": "tester",
                "ISSUE_KEY": "ARCPOC-1",
                "ISSUE_SUMMARY": "Example",
                "ISSUE_URL": "https://example.invalid/browse/ARCPOC-1",
                "OUTPUT_DIR": str(output),
                "VERIFICATION_DIR": str(verified),
                "EXPECTED_BRANCH_NAME": "codex/example",
                "EXPECTED_BASE_SHA": BASE_SHA,
                "JIRA_PUBLISH_MODE": mode,
                "DEFAULT_BRANCH": "master",
                "CODEX_RUNTIME_PATH": str(SCRIPT_DIR.parent.parent),
                "RUNNER_TEMP": str(root / "runner"),
                "GITHUB_OUTPUT": str(github_output),
                "PR_DRAFT": str(expected_draft).lower(),
            }
            if fail_notify:
                env["CODEX_JIRA_PR_NOTIFY_URL"] = "http://127.0.0.1:1/notify"
                env["CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS"] = "1"
            if mode == "repair":
                env["EXPECTED_BRANCH_HEAD_SHA"] = HEAD_SHA
            completed = subprocess.run(
                ["bash", str(JIRA_PUBLISHER)],
                cwd=caller,
                env=env,
                capture_output=True,
                text=True,
            )
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            outputs = github_output.read_text(encoding="utf-8") if github_output.exists() else ""
            return completed, commands, outputs

    def run_jira(
        self,
        *,
        mode: str,
        remote_base: str | Sequence[str] = BASE_SHA,
        remote_head: str | Sequence[str] = "",
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
        pr_exists: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        completed, commands, _ = self.run_jira_with_outputs(
            mode=mode,
            remote_base=remote_base,
            remote_head=remote_head,
            remote_parent=remote_parent,
            remote_tree=remote_tree,
            pr_exists=pr_exists,
        )
        return completed, commands
