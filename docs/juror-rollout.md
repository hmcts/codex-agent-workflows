# Juror rollout runbook

This rollout stays disabled until the shared workflow, all seven callers, the Azure Function and Terraform changes are merged and configured. Activate every Juror repository in one controlled change window.

## GitHub identity and repository configuration

Use a dedicated HMCTS machine user with write access to all seven Juror repositories. A fine-grained token cannot grant more access than its owner already has.

Grant the token access only to the seven Juror repositories, with:

- Metadata: read
- Contents: read and write
- Pull requests: read and write

Configure each Juror repository with:

- Actions secret `CODEX_OPENAI_API_KEY`
- Actions secret `BOT_GITHUB_TOKEN`
- Actions secret `CODEX_JIRA_PR_NOTIFY_URL`
- Actions secret `CODEX_SONAR_TOKEN`
- Actions variable `BOT_PUBLISHER_LOGIN`, set to the exact machine-user login

Configure `hmcts/codex-agent-workflows` with the same `BOT_GITHUB_TOKEN` and `BOT_PUBLISHER_LOGIN` for release pin-update PRs. Keep private reusable-workflow access limited to the HMCTS organisation. Require CODEOWNERS review and prevent force pushes or branch deletion on `main`.

The publisher token is available only to fresh trusted publication jobs. Generated code and verification jobs must not receive the OpenAI API key, publisher token or Jira callback URL.

## Jira field and dispatch rule

Create a required single-select field named `Codex repository` for the JS project with these values:

- `hmcts/juror-er-portal`
- `hmcts/juror-public`
- `hmcts/juror-bureau`
- `hmcts/juror-api`
- `hmcts/juror-scheduler-execution`
- `hmcts/juror-pnc`
- `hmcts/juror-scheduler-api`

The dispatch rule triggers when `codex-ready` is added. It must:

1. Stop if the issue is in a completed status.
2. Stop and comment if `Codex repository` is empty.
3. Transition the issue to `In Progress` when required.
4. Remove `codex-ready` and add `codex-running` before dispatch.
5. POST the issue key, summary, description, status, assignee, labels, issue URL and selected repository to the Azure Function.
6. Restore a usable state and comment when dispatch itself is rejected.

The JS callback rule consumes the Azure Function's project-specific webhook. For `pr-created`, transition to `Peer Review`, add `pr-ready`, remove `codex-running` and comment with the PR and workflow links. For `codex-blocked`, `codex-failed` or `codex-no-changes`, remove `codex-running` and comment with the supplied message and workflow link.

## Platform activation

1. Merge the shared workflow PR after required review.
2. Pin each caller to a commit reachable from shared-repository `main`.
3. Merge the seven caller PRs.
4. Merge and deploy the Azure Function routing change.
5. Configure the JS Jira Automation webhook URL and secret in the Function App.
6. Configure the GitHub repository secrets and variables.
7. Set `enable_juror_runner_scale_sets = true`, review the Terraform plan and apply it.
8. Confirm all seven scale sets register with `minRunners = 0` and `maxRunners = 1`.
9. Enable the Jira dispatch rule.

## Acceptance tests

Run one controlled Jira-to-PR test in every repository. Confirm:

- the required repository field routes to exactly one repository;
- the issue moves to `In Progress` and duplicate events do not start concurrent runs;
- planning uses `gpt-5.6-sol`/`ultra` and auto-approves every actionable structured plan;
- implementation and repair use `gpt-5.6-sol`/`medium`;
- passing verification creates a ready PR;
- exhausted or unavailable verification creates a draft PR with exact evidence;
- impossible work clears `codex-running` and comments with the blockers;
- existing Jenkins, Sonar, functional and smoke checks start normally;
- a repository writer can invoke `/codex-review` on a Codex PR;
- the issue moves to `Peer Review` and receives `pr-ready` when a PR is created;
- runner pods contain no OpenAI or publisher credentials; and
- every scale set returns to zero after its run.

Disable the Jira rule and set `enable_juror_runner_scale_sets = false` if routing, credential isolation, callbacks or scale-to-zero checks fail.
