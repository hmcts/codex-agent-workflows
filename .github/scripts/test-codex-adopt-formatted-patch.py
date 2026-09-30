#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-adopt-formatted-patch.py")


class AdoptFormattedPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.output_dir = self.root / "codex-output"
        self.verified_dir = self.root / "codex-formatted"
        self.output_dir.mkdir()
        self.verified_dir.mkdir()
        self.environment = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        for name in ("app.ts", "old.ts", "tidy.ts", "other.ts"):
            self.write(name, f"export const {name[:-3]} = 1;\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

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

    def write(self, name: str, content: str) -> None:
        (self.repository / name).write_text(content, encoding="utf-8")

    def diff(self, *changes: tuple[str, str | None], renames: bool = False) -> str:
        for name, content in changes:
            if content is None:
                (self.repository / name).unlink()
            else:
                self.write(name, content)
        self.git("add", "-A")
        rename_flag = "-M" if renames else "--no-renames"
        patch = self.git("diff", "--cached", "--binary", rename_flag, "HEAD")
        self.git("reset", "-q", "--hard", "HEAD")
        return patch

    def stage(self, codex_patch: str, formatted_patch: str, recorded_sha: str | None = None) -> None:
        (self.output_dir / "changes.patch").write_text(codex_patch, encoding="utf-8")
        formatted_path = self.verified_dir / "changes.patch"
        formatted_path.write_text(formatted_patch, encoding="utf-8")
        sha = recorded_sha or hashlib.sha256(formatted_path.read_bytes()).hexdigest()
        (self.verified_dir / "verification.env").write_text(
            f"branch_name=codex/test\nbase_sha={'0' * 40}\npatch_sha={sha}\n",
            encoding="utf-8",
        )

    def adopt(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--output-dir",
                str(self.output_dir),
                "--verified-dir",
                str(self.verified_dir),
            ],
            capture_output=True,
            text=True,
        )

    def codex_patch(self) -> str:
        return self.diff(
            ("app.ts", "export const app = 2;   \n"),
            ("new.ts", "export const fresh = 1;  \n"),
            ("old.ts", None),
            ("tidy.ts", "export const tidy = 1;   \n"),
        )

    def test_formatted_patch_replaces_the_codex_patch(self):
        codex = self.codex_patch()
        formatted = self.diff(
            ("app.ts", "export const app = 2;\n"),
            ("new.ts", "export const fresh = 1;\n"),
            ("old.ts", None),
        )
        self.stage(codex, formatted)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            (self.output_dir / "changes.patch").read_text(encoding="utf-8"), formatted
        )
        self.assertIn("Adopted the formatted patch for 3 file(s).", completed.stdout)

    def test_files_outside_the_codex_patch_are_refused(self):
        codex = self.codex_patch()
        formatted = self.diff(
            ("app.ts", "export const app = 2;\n"),
            ("other.ts", "export const other = 'token';\n"),
        )
        self.stage(codex, formatted)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("changes files the Codex patch does not: other.ts", completed.stderr)
        self.assertEqual(
            (self.output_dir / "changes.patch").read_text(encoding="utf-8"), codex
        )

    def test_patch_that_does_not_match_the_recorded_hash_is_refused(self):
        codex = self.codex_patch()
        formatted = self.diff(("app.ts", "export const app = 2;\n"))
        self.stage(codex, formatted, recorded_sha="a" * 64)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("does not match the hash verification recorded", completed.stderr)

    def test_missing_recorded_hash_is_refused(self):
        codex = self.codex_patch()
        formatted = self.diff(("app.ts", "export const app = 2;\n"))
        self.stage(codex, formatted)
        (self.verified_dir / "verification.env").write_text(
            "branch_name=codex/test\n", encoding="utf-8"
        )

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("does not record a patch_sha", completed.stderr)

    def test_symbolic_formatted_patch_is_refused(self):
        codex = self.codex_patch()
        self.stage(codex, codex)
        target = self.root / "elsewhere.patch"
        target.write_text(codex, encoding="utf-8")
        (self.verified_dir / "changes.patch").unlink()
        (self.verified_dir / "changes.patch").symlink_to(target)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("is missing or is not a regular file", completed.stderr)

    def test_missing_formatted_patch_is_refused(self):
        codex = self.codex_patch()
        self.stage(codex, codex)
        (self.verified_dir / "changes.patch").unlink()

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("formatted patch is missing", completed.stderr)

    def test_unreadable_or_empty_patches_are_refused(self):
        codex = self.codex_patch()
        for formatted in ("not a patch\n", ""):
            with self.subTest(formatted=formatted):
                self.stage(codex, formatted)

                completed = self.adopt()

                self.assertEqual(completed.returncode, 1)
                self.assertIn("Formatted patch refused", completed.stderr)

    def test_renames_count_both_paths(self):
        codex = self.diff(
            ("old.ts", None),
            ("moved.ts", "export const old = 1;\n"),
            renames=True,
        )
        self.assertIn("rename from old.ts", codex)
        formatted = self.diff(
            ("old.ts", None),
            ("moved.ts", "export const old = 1;\n"),
        )
        self.assertNotIn("rename from", formatted)
        self.stage(codex, formatted)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rename_from_a_file_outside_the_codex_patch_is_refused(self):
        codex = self.codex_patch()
        formatted = self.diff(
            ("other.ts", None),
            ("new.ts", "export const other = 1;\n"),
            renames=True,
        )
        self.assertIn("rename from other.ts", formatted)
        self.stage(codex, formatted)

        completed = self.adopt()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("changes files the Codex patch does not: other.ts", completed.stderr)


if __name__ == "__main__":
    unittest.main()
