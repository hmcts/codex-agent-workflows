#!/usr/bin/env bash
# Formats the files a Codex patch changed and rebuilds the patch, for callers
# that publish formatted work. It runs in credential-free verification, in a
# checkout whose index holds the applied patch. The formatter runs the
# repository's own tooling, so the rebuilt patch is untrusted: a publish job
# adopts it only after codex-adopt-formatted-patch.py has checked it.

set -euo pipefail

formatter="${CODEX_FORMATTER:-none}"
case "${formatter}" in
  none)
    exit 0
    ;;
  prettier)
    ;;
  *)
    echo "CODEX_FORMATTER must be none or prettier, not '${formatter}'." >&2
    exit 2
    ;;
esac

patch_path="${1:?usage: codex-format-changed-files.sh PATCH_PATH}"

git_safe() {
  git \
    -c core.hooksPath=/dev/null \
    -c core.fsmonitor=false \
    -c credential.helper= \
    -c protocol.file.allow=never \
    "$@"
}

# Prints NUL-separated git paths one per line, sorted, refusing any path a
# newline would split.
path_lines() {
  python3 -I -c '
import sys
paths = [path for path in sys.stdin.buffer.read().split(b"\0") if path]
if any(b"\n" in path for path in paths):
    sys.exit("Refusing to format a path that contains a newline.")
sys.stdout.buffer.write(b"".join(path + b"\n" for path in sorted(set(paths))))
'
}

staged_paths() {
  git_safe diff --cached --name-only -z --no-renames --no-ext-diff "$@" HEAD | path_lines
}

patch_files="$(staged_paths)"
if [[ -z "${patch_files}" ]]; then
  echo "The Codex patch changes no files; nothing to format."
  exit 0
fi

format_files=()
while IFS= read -r path; do
  if [[ -f "${path}" && ! -L "${path}" ]]; then
    format_files+=("${path}")
  fi
done < <(staged_paths --diff-filter=ACM)
if [[ "${#format_files[@]}" -eq 0 ]]; then
  echo "The Codex patch changes no regular files; nothing to format."
  exit 0
fi

yarn_path=""
if [[ -f .yarnrc.yml && ! -L .yarnrc.yml ]]; then
  yarn_path="$(
    sed -n -E "s/^yarnPath:[[:space:]]*[\"']?([^\"'[:space:]]+)[\"']?[[:space:]]*$/\1/p" .yarnrc.yml |
      head -n 1 || true
  )"
fi
case "${yarn_path}" in
  "" | /* | ../* | */../* | *\\*)
    echo "Prettier formatting needs the repository's pinned Yarn release: set yarnPath in .yarnrc.yml to a file inside the repository." >&2
    exit 1
    ;;
esac
if [[ ! -f "${yarn_path}" || -L "${yarn_path}" ]]; then
  echo "The pinned Yarn release ${yarn_path} is missing or symbolic." >&2
  exit 1
fi

untracked_before="$(git_safe ls-files --others --exclude-standard -z | path_lines)"

if [[ ! -x node_modules/.bin/prettier ]]; then
  echo "Installing dependencies so Prettier can run."
  node "${yarn_path}" install --immutable --mode=skip-build
fi

echo "Formatting ${#format_files[@]} changed file(s) with Prettier."
node "${yarn_path}" prettier --write --ignore-unknown -- "${format_files[@]}"

created="$(
  LC_ALL=C comm -13 \
    <(printf '%s\n' "${untracked_before}") \
    <(git_safe ls-files --others --exclude-standard -z | path_lines)
)"
if [[ -n "${created}" ]]; then
  echo "Formatting created files outside the Codex patch:" >&2
  printf '%s\n' "${created}" >&2
  exit 1
fi
unexpected="$(
  LC_ALL=C comm -23 \
    <(git_safe diff --name-only -z --no-renames --no-ext-diff | path_lines) \
    <(printf '%s\n' "${format_files[@]}" | LC_ALL=C sort -u)
)"
if [[ -n "${unexpected}" ]]; then
  echo "Formatting changed files outside the Codex patch:" >&2
  printf '%s\n' "${unexpected}" >&2
  exit 1
fi

git_safe add -- "${format_files[@]}"
formatted_files="$(staged_paths)"
if [[ -z "${formatted_files}" ]]; then
  echo "After formatting, the Codex patch changes nothing." >&2
  exit 1
fi
outside="$(LC_ALL=C comm -23 <(printf '%s\n' "${formatted_files}") <(printf '%s\n' "${patch_files}"))"
if [[ -n "${outside}" ]]; then
  echo "Formatting staged files outside the Codex patch:" >&2
  printf '%s\n' "${outside}" >&2
  exit 1
fi

rebuilt="${patch_path}.formatted"
git_safe diff --cached --binary --full-index --no-renames --no-ext-diff --no-textconv \
  --no-color --src-prefix=a/ --dst-prefix=b/ HEAD >"${rebuilt}"
if [[ ! -s "${rebuilt}" ]]; then
  echo "The rebuilt patch is empty." >&2
  exit 1
fi
mv "${rebuilt}" "${patch_path}"
echo "Rebuilt the Codex patch from the formatted files. The publish job checks it before publishing it."
