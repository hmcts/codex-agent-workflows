#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-format-changed-files.sh")
ADOPTER = Path(__file__).with_name("codex-adopt-formatted-patch.py")
YARN_RELEASE = ".yarn/releases/yarn-4.10.3.cjs"

# Stands in for node running the pinned Yarn release: "install" provides a
# Prettier binary and "prettier --write" strips trailing whitespace.
FAKE_NODE = r"""#!/usr/bin/env bash
set -euo pipefail
yarn_release="$1"
shift
printf '%s %s\n' "${yarn_release}" "$*" >>"${FAKE_NODE_LOG:-${RUNNER_TEMP:?}/node.log}"
case "$1" in
  install)
    mkdir -p node_modules/.bin
    printf '#!/bin/sh\n' >node_modules/.bin/prettier
    chmod +x node_modules/.bin/prettier
    ;;
  prettier)
    shift
    if [[ "$1 $2 $3" != "--write --ignore-unknown --" ]]; then
      echo "unexpected prettier arguments: $*" >&2
      exit 64
    fi
    shift 3
    if [[ "${FAKE_PRETTIER_MODE:-}" == "fail" ]]; then
      echo "SyntaxError: Unexpected token" >&2
      exit 2
    fi
    for path in "$@"; do
      sed -E 's/[[:space:]]+$//' "${path}" >"${path}.tmp"
      mv "${path}.tmp" "${path}"
    done
    case "${FAKE_PRETTIER_MODE:-}" in
      touch-other) printf '# Changed\n' >README.md ;;
      create) printf 'stray\n' >stray.txt ;;
    esac
    ;;
  *)
    echo "unexpected yarn command: $*" >&2
    exit 64
    ;;
esac
"""


class FormatChangedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.patch_path = self.root / "output" / "changes.patch"
        self.patch_path.parent.mkdir()
        self.node_log = self.root / "node.log"
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_node = fake_bin / "node"
        fake_node.write_text(FAKE_NODE, encoding="utf-8")
        fake_node.chmod(0o755)
        self.environment = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_NODE_LOG": str(self.node_log),
            "CODEX_FORMATTER": "prettier",
        }

        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.write(".gitignore", "node_modules/\n")
        self.write(".yarnrc.yml", f"nodeLinker: node-modules\nyarnPath: {YARN_RELEASE}\n")
        self.write(YARN_RELEASE, "// pinned yarn release\n")
        self.write("README.md", "# Test\n")
        self.write("src/app.ts", "export const a = 1;\n")
        self.write("src/old.ts", "export const old = 1;\n")
        self.write("src/tidy.ts", "export const tidy = 1;\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            env=self.environment,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def write(self, relative_path: str, content: str) -> None:
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def stage_codex_patch(self) -> None:
        self.write("src/app.ts", "export const a = 2;   \n")
        self.write("src/new.ts", "export const b = 1;  \n")
        self.write("src/tidy.ts", "export const tidy = 1;    \n")
        (self.repository / "src" / "old.ts").unlink()
        self.git("add", "-A")
        self.patch_path.write_text(
            self.git("diff", "--cached", "--binary", "HEAD"), encoding="utf-8"
        )

    def run_formatter(self, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.patch_path)],
            cwd=self.repository,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
        )

    def node_calls(self) -> list[str]:
        if not self.node_log.exists():
            return []
        return self.node_log.read_text(encoding="utf-8").splitlines()

    def patch_paths(self, patch: str) -> set[str]:
        completed = subprocess.run(
            ["git", "apply", "--numstat", "-"],
            cwd=self.root,
            env=self.environment,
            input=patch,
            check=True,
            capture_output=True,
            text=True,
        )
        return {line.split("\t")[2] for line in completed.stdout.splitlines()}

    def test_none_leaves_the_patch_alone(self):
        self.stage_codex_patch()
        original = self.patch_path.read_bytes()

        completed = self.run_formatter(CODEX_FORMATTER="none")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.patch_path.read_bytes(), original)
        self.assertEqual(self.node_calls(), [])

    def test_unknown_formatter_is_refused(self):
        self.stage_codex_patch()

        completed = self.run_formatter(CODEX_FORMATTER="black")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("CODEX_FORMATTER must be none or prettier", completed.stderr)

    def test_prettier_formats_changed_files_and_rebuilds_the_patch(self):
        self.stage_codex_patch()

        completed = self.run_formatter()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        patch = self.patch_path.read_text(encoding="utf-8")
        self.assertEqual(
            self.patch_paths(patch), {"src/app.ts", "src/new.ts", "src/old.ts"}
        )
        self.assertIn("+export const a = 2;\n", patch)
        self.assertIn("+export const b = 1;\n", patch)
        self.assertIn("deleted file mode", patch)
        self.assertNotIn("src/tidy.ts", patch)
        self.assertEqual(
            self.node_calls(),
            [
                f"{YARN_RELEASE} install --immutable --mode=skip-build",
                f"{YARN_RELEASE} prettier --write --ignore-unknown -- "
                "src/app.ts src/new.ts src/tidy.ts",
            ],
        )

        self.git("reset", "-q", "--hard", "HEAD")
        self.git("apply", "--index", "--binary", str(self.patch_path))
        self.assertEqual(
            (self.repository / "src" / "app.ts").read_text(encoding="utf-8"),
            "export const a = 2;\n",
        )

    def test_rebuilt_patch_passes_publication_adoption(self):
        self.stage_codex_patch()
        codex_dir = self.root / "codex-output-original"
        codex_dir.mkdir()
        (codex_dir / "changes.patch").write_bytes(self.patch_path.read_bytes())

        completed = self.run_formatter()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        patch_sha = hashlib.sha256(self.patch_path.read_bytes()).hexdigest()
        (self.patch_path.parent / "verification.env").write_text(
            f"patch_sha={patch_sha}\n", encoding="utf-8"
        )

        adopted = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ADOPTER),
                "--output-dir",
                str(codex_dir),
                "--verified-dir",
                str(self.patch_path.parent),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertEqual(
            (codex_dir / "changes.patch").read_bytes(), self.patch_path.read_bytes()
        )

    def test_installed_prettier_skips_the_install(self):
        self.stage_codex_patch()
        prettier = self.repository / "node_modules" / ".bin" / "prettier"
        prettier.parent.mkdir(parents=True)
        prettier.write_text("#!/bin/sh\n", encoding="utf-8")
        prettier.chmod(0o755)

        completed = self.run_formatter()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(self.node_calls()), 1)
        self.assertIn(" prettier --write ", self.node_calls()[0])

    def test_changes_to_other_tracked_files_are_refused(self):
        self.stage_codex_patch()
        original = self.patch_path.read_bytes()

        completed = self.run_formatter(FAKE_PRETTIER_MODE="touch-other")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Formatting changed files outside the Codex patch", completed.stderr)
        self.assertIn("README.md", completed.stderr)
        self.assertEqual(self.patch_path.read_bytes(), original)

    def test_new_untracked_files_are_refused(self):
        self.stage_codex_patch()

        completed = self.run_formatter(FAKE_PRETTIER_MODE="create")

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Formatting created files outside the Codex patch", completed.stderr)
        self.assertIn("stray.txt", completed.stderr)

    def test_formatter_failure_fails_verification(self):
        self.stage_codex_patch()

        completed = self.run_formatter(FAKE_PRETTIER_MODE="fail")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("SyntaxError", completed.stderr)

    def test_patch_reverted_entirely_by_formatting_is_refused(self):
        self.write("src/tidy.ts", "export const tidy = 1;   \n")
        self.git("add", "-A")
        self.patch_path.write_text(
            self.git("diff", "--cached", "--binary", "HEAD"), encoding="utf-8"
        )

        completed = self.run_formatter()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("After formatting, the Codex patch changes nothing", completed.stderr)

    def test_symbolic_links_are_not_formatted(self):
        self.stage_codex_patch()
        (self.repository / "src" / "link.ts").symlink_to("app.ts")
        self.git("add", "-A")

        completed = self.run_formatter()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("src/link.ts", self.node_calls()[-1])
        self.assertIn("src/link.ts", self.patch_paths(self.patch_path.read_text(encoding="utf-8")))

    def test_yarn_release_must_stay_inside_the_repository(self):
        self.stage_codex_patch()
        for yarn_path in ("../yarn.cjs", "/usr/bin/yarn", ".yarn/../../yarn.cjs", ""):
            with self.subTest(yarn_path=yarn_path):
                self.write(".yarnrc.yml", f"yarnPath: {yarn_path}\n")

                completed = self.run_formatter()

                self.assertEqual(completed.returncode, 1)
                self.assertIn("pinned Yarn release", completed.stderr)
                self.assertEqual(self.node_calls(), [])

    def test_patch_without_changed_regular_files_needs_no_formatter(self):
        (self.repository / "src" / "old.ts").unlink()
        self.git("add", "-A")

        completed = self.run_formatter()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("no regular files", completed.stdout)
        self.assertEqual(self.node_calls(), [])


if __name__ == "__main__":
    unittest.main()
