# Caller contract

## Required dispatch inputs

The caller forwards Jira issue key, summary, description, status, assignee and URL. It also forwards the optional `initiatorDisplayName` supplied by Jira Automation, and supplies its runner group, repository-scoped runner label, required status context and the client ID of the HMCTS-owned Codex GitHub App.

The initiating display name is used only by trusted collection jobs to add traceability to the PR body. It is not included in model prompts. Missing or invalid values render as `Not supplied by Jira Automation`; callers must not substitute the assignee or reporter.

## Optional publication policies

Both reusable workflows take two optional inputs. The defaults keep the current behaviour.

- `publish_policy`: `draft-on-failure` (default) or `verified-only`.
  - With `draft-on-failure`, work that fails verification is published as a draft PR, and Jira hears about every outcome: `pr-created`, `draft-pr-created`, blocked, no changes and failed.
  - With `verified-only`, only verified work is published and Jira hears only `pr-created`. A plan that is blocked, verification that fails, or a published PR that fails its required status after the repair attempt ends the run as failed, without a draft PR, a draft conversion or a Jira failure callback.
- `cannot_be_built`: `stop` (default) or `repair`.
  - With `stop`, a required status reporting that the commit cannot be built ends the run without a repair.
  - With `repair`, the post-PR repair attempt is spent on it.

Any other value fails the run before anything else starts.

## Required secrets

- `CODEX_OPENAI_API_KEY`
- `CODEX_GITHUB_APP_PRIVATE_KEY`
- `CODEX_JIRA_PR_NOTIFY_URL`

A caller either passes these as repository secrets or keeps them only in the environments it names: `CODEX_OPENAI_API_KEY` in `model_environment`, and the App key and `CODEX_JIRA_PR_NOTIFY_URL` in `publisher_environment`. The caller still maps all three; an environment secret takes precedence inside the job that declares that environment. The shared workflows declare them optional so an empty caller value is accepted, and every job that uses one fails immediately if it is still empty.

The shared workflows have no Sonar integration of their own. After publication, the workflow waits for the required Jenkins status and treats the repository's own required Jenkins, Sonar and GitHub status checks as authoritative.

Each trusted publisher job mints a short-lived GitHub App installation token restricted to the caller repository. The App bot identity is verified and derived at runtime; no publisher PAT or stored login is required. Secrets are unavailable to generated code and credential-free verification jobs.

Callers must pass `CODEX_JIRA_PR_NOTIFY_URL` to both the implementation and PR-review reusable workflows. Terminal Jenkins or credential-free verification failures return the PR to draft, attach the final evidence and notify Jira from a fresh trusted job.

The trusted plan-validation job retains the complete normalised `plan.json`, its SHA-256 file and the approved path list as the `codex-validated-plan` workflow artefact for 30 days. Planning or trusted validation failures attempt a terminal Jira callback containing the workflow run URL before implementation starts.

The same App may authenticate Azure Function workflow dispatches, provided it has `Actions: read and write` and is installed on every caller repository. Dispatch tokens are minted separately and restricted to the selected repository.

## Review feedback selection

`codex-review-feedback` takes an optional `review_selection` input that sets which review feedback a `/codex-review` command addresses. Only reviews and inline comments from users with write, maintain or admin permission are used in either mode.

- `latest-review` (default): the newest actionable review, with its inline comments.
- `current-reviews`: every change-request or comment review of the PR's current head, submitted by the time the command was posted. A reviewer's feedback is left out if that reviewer has approved since.
  - Inline comments are included only if they still apply to the current head and were not written or edited after the command.
  - The command's author must also have write access.
  - The collected feedback is capped at 64 KiB.
  - If the PR head moves after the feedback is collected, the run stops and asks for a fresh `/codex-review`.

Any other value fails the run before anything else starts.

## Required repository files

Callers retain only repository-owned configuration and verification:

- `.github/workflows/codex_jira_dispatch.yml`
- `.github/workflows/codex_pr_review.yml`
- `bin/codex-local-pipeline.sh`
- `AGENTS.md`

Trusted planning, collection, repair, publication and security-validation scripts are loaded from this repository by `.github/actions/runtime` at an immutable commit SHA. Caller repositories must not copy or override that runtime.

Callers move to a new release only through `Update caller workflow pins`. An operator dispatches it with the reviewed `release_sha` and a caller set: `juror`, `appreg` or `all`. `.github/config/caller-repositories.json` lists each caller with the following:

- its caller set and default branch;
- its runner group;
- the model and publisher environments, where it keeps its secrets only in environments.

The updater pins both wrappers and keeps that runner group and those environments. A repository whose wrappers do not call the shared workflows yet is skipped.

The local pipeline must be credential-free and must not fetch or execute untrusted remote content. Existing branch-required Jenkins, Sonar, functional and smoke checks remain authoritative after publication.

## Optional verification settings

Both reusable workflows take three optional inputs for frontend repositories. The defaults leave verification unchanged.

- `formatter`: `none` (default) or `prettier`. With `prettier`, credential-free verification runs Prettier on the files Codex changed, after the credential safety gate and before the local pipeline. Prettier runs through the Yarn release that `yarnPath` in `.yarnrc.yml` pins; dependencies are installed with `--immutable --mode=skip-build` when Prettier is missing. Verification then checks the formatted files and records the formatted patch.
  - Formatting may not change, create or stage any file outside the Codex patch, and it may not leave the patch empty. It may return a file to its original content, which drops that file from the patch.
  - The publish job treats the formatted patch as untrusted. It adopts the patch only when its hash matches the recorded verification and it touches no file outside the Codex patch, counting rename and copy sources. The job then runs the credential safety gate on the formatted tree before minting a token. The formatted output feeds post-publication verification and repair.
  - Draft PRs are still built from Codex's unformatted patch.
  - Any value other than `none` or `prettier` fails the run before anything else starts.
- `node_version_file`: a repository file that pins the Node.js version for verification, such as `.nvmrc`. When set, it replaces `node_version`.
- `frontend_fast_command`: passed to the local pipeline as `FRONTEND_FAST_COMMAND` in every credential-free verification, such as `yarn lint`. When empty, the pipeline keeps its own default.

## Publication behaviour

Passing verification produces a ready-for-review PR. When all available repair attempts fail, the latest structurally valid patch is published as a draft with the verification failure attached. Sensitive changes are allowed but must be highlighted in the PR body.

## Plan policy

`plan_policy` on the implementation workflow is `standard` (default) or `strict`, and any other value fails plan validation.

- **Forbidden paths:** a strict plan fails validation if any planned or sensitive path is under `.github`, or is part of the tooling that runs verification. That tooling is `bin/`, `buildSrc/`, `gradle/` and `.yarn/`, and the repository-root `build.gradle`, `settings.gradle`, `gradle.properties`, `gradlew`, `gradlew.bat`, `init.gradle`, `package.json`, `yarn.lock`, `.yarnrc.yml`, `.nvmrc`, `.pnp.cjs` and `.pnp.loader.mjs`.
- **Not ready:** a strict plan that is high-risk or cross-system is marked not ready, with the reason as its blocker, so no implementation runs.

With `standard`, those paths are allowed when the plan lists them in `sensitive_files`, and high-risk or cross-system plans can proceed.
