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

## Required repository files

Callers retain only repository-owned configuration and verification:

- `.github/workflows/codex_jira_dispatch.yml`
- `.github/workflows/codex_pr_review.yml`
- `bin/codex-local-pipeline.sh`
- `AGENTS.md`

Trusted planning, collection, repair, publication and security-validation scripts are loaded from this repository by `.github/actions/runtime` at an immutable commit SHA. Caller repositories must not copy or override that runtime.

The local pipeline must be credential-free and must not fetch or execute untrusted remote content. Existing branch-required Jenkins, Sonar, functional and smoke checks remain authoritative after publication.

## Publication behaviour

Passing verification produces a ready-for-review PR. When all available repair attempts fail, the latest structurally valid patch is published as a draft with the verification failure attached. Sensitive changes are allowed but must be highlighted in the PR body.

## Plan policy

`plan_policy` on the implementation workflow is `standard` (default) or `strict`, and any other value fails plan validation.

- **Forbidden paths:** a strict plan fails validation if any planned or sensitive path is under `.github`, or is part of the tooling that runs verification. That tooling is `bin/`, `buildSrc/`, `gradle/` and `.yarn/`, and the repository-root `build.gradle`, `settings.gradle`, `gradle.properties`, `gradlew`, `gradlew.bat`, `init.gradle`, `package.json`, `yarn.lock`, `.yarnrc.yml`, `.nvmrc`, `.pnp.cjs` and `.pnp.loader.mjs`.
- **Not ready:** a strict plan that is high-risk or cross-system is marked not ready, with the reason as its blocker, so no implementation runs.

With `standard`, those paths are allowed when the plan lists them in `sensitive_files`, and high-risk or cross-system plans can proceed.
