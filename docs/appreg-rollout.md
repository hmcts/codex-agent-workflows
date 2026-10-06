# Apps Reg rollout runbook

This runbook moves `hmcts/appreg-api` and `hmcts/appreg-frontend` from their local Codex workflows onto this repository's shared workflows. Juror is already on them (see [juror-rollout.md](juror-rollout.md)). This runbook covers what differs for Apps Reg.

Apps Reg keeps its behaviour through opt-in inputs. Their defaults are Juror's behaviour, so releasing them changes nothing for Juror.

## Apps Reg's settings

Both repositories pass these inputs to both caller wrappers, unless a row says otherwise.

| Input | appreg-api | appreg-frontend | What it keeps |
|---|---|---|---|
| `runner_group` | `appreg-codex` | `appreg-codex` | Apps Reg's runner group |
| `runner_label` | `codex-pilot-azure-aks` | `codex-frontend-azure-aks` | Each repository's scale set |
| `model_environment` | `codex-model` | `codex-model` | Where `CODEX_OPENAI_API_KEY` lives |
| `publisher_environment` | `codex-publisher` | `codex-publisher` | Where the App key and `CODEX_JIRA_PR_NOTIFY_URL` live |
| `java_version` | `"21"` | `"21"` | |
| `node_version_file` | not set | `.nvmrc` | Node from `.nvmrc` |
| `formatter` | not set | `prettier` | Prettier on the files Codex changed |
| `frontend_fast_command` | not set | `yarn lint` | `yarn lint` instead of `yarn cichecks` |
| `publish_policy` | `verified-only` | `verified-only` | Only verified work is published; Jira hears only `pr-created` |
| `cannot_be_built` | `repair` | `repair` | The post-PR repair is spent on an unbuildable commit |
| `plan_policy` (Jira wrapper only) | `strict` | `strict` | No `.github`, build or dependency changes, and no high-risk or cross-system plans |
| `review_selection` (review wrapper only) | `current-reviews` | `current-reviews` | Every writer review of the current head |
| `review_repair_rounds` (review wrapper only) | not set | `"3"` | Up to three repairs before a review update is published |

[caller-contract.md](caller-contract.md) describes each input. Apps Reg uses the exact `/codex-review` command, like Juror. The merge-conflict command is retired.

## Prerequisites

1. **Shared release.** Every input in the table is on `main`, along with the gate's `codex/` skip condition and the caller-set updater. Record the reviewed `main` commit to release as the release SHA.
2. **Runner group.** PlatOps adds this repository's seven model workflows at the release SHA to `appreg-codex`'s workflow access list:
   - `codex-plan.yml`
   - `codex-generate.yml`
   - `codex-repair-round.yml`
   - `codex-post-repair.yml`
   - `codex-review-generate.yml`
   - `codex-review-repair.yml`
   - `codex-review-repair-round.yml`

   Keep the eight local master paths until cutover is verified, so the local workflows keep working. This change is ARCPOC-1674 and needs its owner's sign-off.
3. **Environments.** The three secrets stay where they are: `CODEX_OPENAI_API_KEY` in `codex-model`, and the App key and `CODEX_JIRA_PR_NOTIFY_URL` in `codex-publisher`. Neither repository needs repository-level copies. The shared workflows accept the empty caller values and read the environment secrets.
4. **Runner targets.** `.github/config/runner-targets.json` already lists both scale-set labels under `appreg-codex`.

## Repository changes

Raise one PR in each repository. Apps Reg reviews and merges them.

1. **Jira wrapper.** Replace `.github/workflows/codex_jira_dispatch.yml` with a thin caller of `codex-implement.yml` at the release SHA.
   - Keep the file name: the Azure Function dispatches it by name (`src/projectPolicies.js`).
   - Keep the `workflow_dispatch` inputs, the concurrency group and the three secret mappings.
   - Add the inputs from the table.
2. **Review wrapper.** Add `.github/workflows/codex_pr_review.yml` as a thin caller of `codex-review-feedback.yml` at the release SHA, and delete `codex_pr_review_feedback.yml`. It must match the contract the credential safety gate checks:
   - `on: issue_comment` with exactly `types: [created]`;
   - the exact `/codex-review` job condition;
   - read-only permissions;
   - literal inputs;
   - exactly the three secrets.

   Juror's wrapper is the model.
3. **Merge-conflict command.** Delete `codex_merge_conflict_resolution.yml`.
4. **Preview and CodeQL jobs.** In `close_pr.yaml` and `portal_redirect_pr.yml` (api), and `close-pr.yml`, `on-pr.yml` and `codeql.yaml` (frontend), add exactly `if: ${{ !startsWith(github.head_ref, 'codex/') }}` to each job the gate reports. These workflows use Azure secrets or `security-events: write` on `pull_request`.
   - The condition must be the job's whole `if`, written on one line as shown, and the workflows must stay reachable only from `pull_request` or `pull_request_target`.
   - A job that only `needs` a guarded job needs the condition too.
5. **Local pipeline.** Remove the checks in `bin/codex-local-pipeline.sh` that assert the local Codex workflow structure, roughly lines 124 to 755. Keep the Flyway guards and the Gradle or Yarn steps. The pipeline must keep reading `FRONTEND_FAST_COMMAND`.
6. **Prompt rules.** Move the repository-specific rules from the local prompts into `AGENTS.md`:
   - appreg-api: Checkstyle and Spotless, and `.appregtmp`;
   - appreg-frontend: Angular, accessibility and the HMCTS design system.
7. **Local runtime.** Delete the local Codex scripts, schemas and their tests, and `codex_trust_checks.yml`. The shared workflows load their runtime from this repository and carry their own trust contract.
   - The local settings audit and trust-check tests name the local workflows, so they go too.
   - Keep `codex_runner_smoke.yml` and the preflight script it runs only if you want the runner smoke test, and keep its access-list entry with them.

Before merging, run the release's gate on each repository's workflows as they will stand:

```bash
ruby .github/scripts/check-codex-pr-safety.rb --repository-root <caller checkout> --trusted-repository-root <caller checkout>
```

It must pass. The shared workflows also run it on every Codex patch and before every publication.

## Cutover

1. Merge the two repository PRs.
2. Move a codex-ready ARCPOC ticket in each repository and follow the run:
   - the plan passes strict validation;
   - verification formats (frontend) and passes;
   - a ready PR is published and Jira receives `pr-created`.
3. Post `/codex-review` on each PR after leaving a review from a writer. Codex addresses the current reviews and pushes one verified update. On the frontend, a failed verification is repaired before anything is pushed.
4. Once both repositories work end to end, PlatOps removes the eight local master paths from `appreg-codex`'s access list.

To roll back before step 4, revert the repository PR. The local workflows and their access-list entries are still in place.

## Later releases

Apps Reg takes shared releases through `Update caller workflow pins`, dispatched with the reviewed `release_sha` and `callers: appreg`. `callers: all` releases to both teams.

The updater raises one PR per repository and keeps each caller's `appreg-codex` runner group and `codex-model`/`codex-publisher` environments. It skips a repository whose wrappers do not call the shared workflows yet, so a release dispatched before cutover changes nothing in Apps Reg.

Each new release SHA needs the seven access-list entries at that SHA before the pin PRs merge.
