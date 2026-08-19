# Juror rollout runbook

This rollout stays disabled until the shared workflow, all seven callers, the Azure Function and Terraform changes are merged and configured. Activate every Juror repository in one controlled change window.

## GitHub identity and repository configuration

Create an HMCTS-owned GitHub App for Codex publication and install it only on `hmcts/codex-agent-workflows`, the seven Juror repositories and the two Apps Reg repositories. Grant the App:

- Metadata: read
- Contents: read and write
- Pull requests: read and write
- Issues: read and write
- Workflows: read and write
- Actions: read and write

Configure each Juror repository with:

- Actions secret `CODEX_OPENAI_API_KEY`
- Actions secret `CODEX_GITHUB_APP_PRIVATE_KEY`
- Actions secret `CODEX_JIRA_PR_NOTIFY_URL`
- Actions secret `CODEX_SONAR_TOKEN`
- Actions variable `CODEX_GITHUB_APP_CLIENT_ID`

Configure `hmcts/codex-agent-workflows` with the same App client ID and private key for release pin-update PRs. Keep private reusable-workflow access limited to the HMCTS organisation. Require CODEOWNERS review and prevent force pushes or branch deletion on `main`.

The App private key and short-lived installation token are available only to fresh trusted token-minting and publication steps. Generated code and verification jobs must not receive the OpenAI API key, App credentials, publisher token or Jira callback URL.

## Jira repository labels and dispatch rule

Route each JS issue using exactly one of these labels:

| Jira label | GitHub repository |
|---|---|
| `codex-repo-juror-er-portal` | `hmcts/juror-er-portal` |
| `codex-repo-juror-public` | `hmcts/juror-public` |
| `codex-repo-juror-bureau` | `hmcts/juror-bureau` |
| `codex-repo-juror-api` | `hmcts/juror-api` |
| `codex-repo-juror-scheduler-execution` | `hmcts/juror-scheduler-execution` |
| `codex-repo-juror-pnc` | `hmcts/juror-pnc` |
| `codex-repo-juror-scheduler-api` | `hmcts/juror-scheduler-api` |

The dispatch rule triggers when `codex-ready` is added. It must:

1. Stop if the issue is in a completed status.
2. Stop and comment unless exactly one approved `codex-repo-*` label is present.
3. Transition the issue to `In Progress` when required.
4. Remove `codex-ready` and add `codex-running` before dispatch.
5. POST the issue key, summary, description, status, assignee, labels, issue URL and initiating display name to the Azure Function. The Function resolves the repository from the label.
6. Restore a usable state and comment when dispatch itself is rejected.

The JS callback rule consumes the Azure Function's project-specific webhook. For `pr-created`, transition to `Peer Review`, add `pr-ready`, remove `codex-running` and comment with the PR and workflow links. For `codex-blocked`, `codex-failed` or `codex-no-changes`, remove `codex-running` and comment with the supplied message and workflow link.

## Platform activation

1. Merge the shared workflow PR after required review and record the resulting 40-character `main` release SHA.
2. Onboard the two caller wrappers in every Juror repository while Jira dispatch remains disabled. Each onboarding PR must pin exactly that reviewed release SHA; do not use a feature-branch head or an unreviewed shared commit.
3. Merge the seven caller onboarding PRs.
4. After all wrappers exist on each caller's `master`, manually dispatch `Update caller workflow pins` with the recorded release SHA. This post-onboarding dispatch is mandatory even if the scheduled retry has already run.
5. Review and merge every caller pin-update PR raised by the updater. A scheduled retry runs every six hours so a repository that previously returned an explicit wrapper 404 is reconsidered after onboarding.
6. Verify both wrappers in all seven callers pin exactly the recorded release SHA before enabling any trigger. A caller with a missing wrapper, a different pin or a failed updater matrix job is not accepted for activation.
7. Merge and deploy the Azure Function routing change.
8. Configure the JS Jira Automation webhook URL and secret in the Function App.
9. Configure the GitHub repository secrets and variables.
10. Set `enable_juror_runner_scale_sets = true`, review the Terraform plan and apply it.
11. Confirm all seven scale sets register with `minRunners = 0` and `maxRunners = 1`.
12. Enable the Jira dispatch rule.

## Acceptance tests

Run one controlled Jira-to-PR test in every repository. Confirm:

- exactly one approved repository label routes to the intended repository;
- the issue moves to `In Progress` and duplicate events do not start concurrent runs;
- planning uses `gpt-5.6-sol`/`ultra` and auto-approves every actionable structured plan;
- implementation and repair use `gpt-5.6-sol`/`medium`;
- passing verification creates a ready PR;
- exhausted or unavailable verification creates a draft PR with exact evidence;
- impossible work clears `codex-running` and comments with the blockers;
- existing Jenkins, Sonar, functional and smoke checks start normally;
- a repository writer can invoke `/codex-review` on a Codex PR;
- the issue moves to `Peer Review` and receives `pr-ready` when a PR is created;
- runner pods contain no OpenAI or publisher credentials;
- both caller wrappers pin the recorded, reviewed shared `main` release SHA and the post-onboarding updater dispatch completed without skipped or failed repositories; and
- every scale set returns to zero after its run.

Disable the Jira rule and set `enable_juror_runner_scale_sets = false` if routing, credential isolation, callbacks or scale-to-zero checks fail.
