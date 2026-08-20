#!/usr/bin/env python3

from publisher_test_constants import *  # noqa: F403


class PublisherGitRaceMixin:
    @staticmethod
    def run_real_git(
        cwd: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={
                **os.environ,
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            capture_output=True,
            text=True,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed:\n{completed.stdout}\n{completed.stderr}"
            )
        return completed

    def make_bare_remote(self, root: Path) -> tuple[Path, Path, Path]:
        remote = root / "remote.git"
        publisher = root / "publisher"
        racer = root / "racer"
        self.run_real_git(root, "init", "--bare", str(remote))
        self.run_real_git(root, "init", str(publisher))
        self.run_real_git(publisher, "config", "user.name", "Publisher")
        self.run_real_git(publisher, "config", "user.email", "publisher@example.invalid")
        (publisher / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.run_real_git(publisher, "add", "seed.txt")
        self.run_real_git(publisher, "commit", "-m", "Seed")
        self.run_real_git(publisher, "branch", "-M", "master")
        self.run_real_git(publisher, "remote", "add", "origin", str(remote))
        self.run_real_git(publisher, "push", "-u", "origin", "master")
        self.run_real_git(root, "clone", str(remote), str(racer))
        self.run_real_git(racer, "config", "user.name", "Racer")
        self.run_real_git(racer, "config", "user.email", "racer@example.invalid")
        return remote, publisher, racer

    def commit_real_file(
        self,
        repository: Path,
        path: str,
        content: str,
        message: str,
    ) -> str:
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.run_real_git(repository, "add", path)
        self.run_real_git(repository, "commit", "-m", message)
        return self.run_real_git(repository, "rev-parse", "HEAD").stdout.strip()

    def remote_branch_sha(self, remote: Path, branch: str) -> str:
        return self.run_real_git(
            remote,
            "rev-parse",
            f"refs/heads/{branch}",
        ).stdout.strip()

    def assert_exact_lease_rejects_atomic_race(self, branch: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            remote, publisher, racer = self.make_bare_remote(Path(temporary_directory))
            self.run_real_git(publisher, "checkout", "-b", branch, "master")
            expected_sha = self.commit_real_file(
                publisher,
                "expected.txt",
                f"{branch} expected\n",
                "Create expected branch head",
            )
            self.run_real_git(publisher, "push", "-u", "origin", branch)
            self.commit_real_file(
                publisher,
                "publisher.txt",
                f"{branch} publisher\n",
                "Prepare publisher update",
            )

            self.run_real_git(racer, "fetch", "origin", branch)
            self.run_real_git(racer, "checkout", "-B", branch, f"origin/{branch}")
            moved_sha = self.commit_real_file(
                racer,
                "racer.txt",
                f"{branch} racer\n",
                "Move remote branch",
            )
            self.run_real_git(racer, "push", "origin", branch)

            completed = self.run_real_git(
                publisher,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_sha}",
                "origin",
                branch,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(self.remote_branch_sha(remote, branch), moved_sha)

    def assert_empty_lease_rejects_atomic_race(self, branch: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            remote, publisher, racer = self.make_bare_remote(Path(temporary_directory))
            self.run_real_git(publisher, "checkout", "-b", branch, "master")
            self.commit_real_file(
                publisher,
                "publisher.txt",
                f"{branch} publisher\n",
                "Prepare initial publication",
            )

            self.run_real_git(racer, "checkout", "-b", branch, "origin/master")
            moved_sha = self.commit_real_file(
                racer,
                "racer.txt",
                f"{branch} racer\n",
                "Create remote branch during publication",
            )
            self.run_real_git(racer, "push", "origin", branch)

            completed = self.run_real_git(
                publisher,
                "push",
                f"--force-with-lease=refs/heads/{branch}:",
                "origin",
                branch,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(self.remote_branch_sha(remote, branch), moved_sha)
