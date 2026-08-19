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

## Generated-revision event policy

The immutable structural checker runs at both sides of publication. Credential-free verification and every secret-bearing publication job make the same two-snapshot policy decision: they apply the generated patch to its exact candidate base, retain a separate immutable trusted default/base tree, and pass both roots to one checker invocation. The checker joins candidate event roots and upstream names to candidate listeners and unchanged trusted default-branch listeners. This prevents a generated workflow from introducing an unsafe upstream while a patch deletion, rename or weakened listener copy hides the privileged listener that remains active on the default branch. Verification and publication fail closed if either snapshot cannot be materialized and parsed; publication completes this check before minting a GitHub App token.

The policy treats these automatic event roots as able to reach an unreviewed revision: generated `codex/**` pushes and branch creation; `pull_request`, `pull_request_target`, review, review-comment, review-thread and PR issue-comment events; merge-group revisions; commit comments, check runs, check suites and statuses. It also resolves every local `workflow_run` listener by static workflow name and propagates exposure through the complete listener graph across the two snapshots. Missing or duplicate names, dynamic filters, unsupported filters and listener cycles fail closed. A first-hop branch filter can stop branch-tainted `codex/**` push/PR runs only when it statically excludes every generated branch. Once a `workflow_run` listener is reachable, its definition executes from the default branch and later branch filters cannot clear that transitive exposure.

The only credential-bearing automatic-event exception is the caller's `/codex-review` command wrapper loaded from the caller's default branch by `issue_comment`. It must use only `issue_comment` with exactly `types: [created]`, and its reusable job must require a PR comment whose body equals `/codex-review` and whose `author_association` is exactly one of `COLLABORATOR`, `MEMBER` or `OWNER`. `pull_request_review` and `pull_request_review_comment` are not accepted because a proposed wrapper can run for those events before shared validation. The candidate wrapper must call `hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml` at the same reviewed 40-character SHA as the immutable trusted wrapper. Every security-sensitive input must equal the trusted wrapper value, `sonar_host_url` must be exactly `https://sonarcloud.io` with no alternate scheme, credentials, port, path, query or fragment, and only the four exact review secrets may be mapped. The wrapper cannot contain executable steps, write/OIDC permissions, an environment or event-derived inputs. The pinned shared workflow independently checks writer permission and the explicit command before it reads the generated revision, and model-facing jobs retain their existing credential isolation.

Review-feedback publication resolves and checks out the current default branch again in each credential-bearing publication job, validates the candidate patch against that fresh immutable workflow tree before minting the App token, and passes the exact default SHA to the trusted publisher. The publisher resolves the default ref again after preparing the feedback commit and immediately before push or recovery acceptance. An unavailable or changed default ref fails closed; the SHA captured before the model run is not reused as publication authority.

Events intentionally outside the automatic generated-revision set are limited by their trust boundary:

- `schedule` loads the immutable default-branch definition and has no untrusted revision payload;
- `workflow_dispatch` requires a trusted operator, and `repository_dispatch` requires an authenticated trusted service such as the configured Jira router;
- release, deployment, package and repository-administration events require a credentialed actor that generated verification jobs do not possess;
- issue/discussion/community events that do not identify a PR or commit carry content but no executable revision; and
- push filters proven disjoint from `codex/**`, plus tags-only pushes, cannot be activated by generated branch publication.

These exclusions do not authorize a trusted workflow to turn arbitrary event fields into a checkout ref. Any new event that can be emitted by a generated branch or can identify its PR, commit or check must be added to the structural root model before activation.

## Platform activation

1. Merge the shared workflow PR after required review and record the resulting 40-character `main` release SHA.
2. Make PR verification credential-free in every Juror caller before onboarding or activation. Every job that executes PR code must declare explicit least-privilege read-only `permissions` and must not receive secrets, OIDC, an environment or any write permission. Move credentialed or write operations to events and branch filters that cannot match generated `codex/**` branches.
3. In `hmcts/juror-scheduler-api`, make Azure login and ACR authentication push-only on trusted non-generated branches, or separate them from PR verification in a workflow with equivalent branch restrictions. They must never run for `pull_request`, `pull_request_target` or a push that can match `codex/**`.
4. Raise trusted caller validation PRs and require each caller's normal PR check and the exact shared Codex credential safety gate to pass. Merge the prerequisite safety changes first. The shared workflow intentionally fails closed for every caller until its external prerequisite is present on `master`; do not bypass the gate.
5. Onboard the two caller wrappers in every Juror repository while Jira dispatch remains disabled. Each onboarding PR must pin exactly that reviewed release SHA; do not use a feature-branch head or an unreviewed shared commit. Every review wrapper must use the exact safe `issue_comment.created` `/codex-review` command and author-association gate above. Existing review-event wrappers remain intentionally blocked until replaced.
6. Merge the seven caller onboarding PRs.
7. Run the immutable shared `check-codex-pr-safety.rb` from the recorded release SHA against the live `master` checkout of all seven callers. All seven exact checks must pass before activation; a local approximation, documentation exception or check against an onboarding branch is not sufficient.
8. After all wrappers exist on each caller's `master`, manually dispatch `Update caller workflow pins` with the recorded release SHA. This post-onboarding dispatch is mandatory even if the scheduled retry has already run.
9. Review and merge every caller pin-update PR raised by the updater. A scheduled retry runs every six hours so a repository that previously returned an explicit wrapper 404 is reconsidered after onboarding.
10. Verify both wrappers in all seven callers pin exactly the recorded release SHA before enabling any trigger. A caller with a missing wrapper, a different pin or a failed updater matrix job is not accepted for activation.
11. Merge and deploy the Azure Function routing change.
12. Configure the JS Jira Automation webhook URL and secret in the Function App.
13. Configure the GitHub repository secrets and variables.
14. Set `enable_juror_runner_scale_sets = true`, review the Terraform plan and apply it.
15. Confirm all seven scale sets register with `minRunners = 0` and `maxRunners = 1`.
16. Enable the Jira dispatch rule.

The read-only live audit on 2026-08-19 intentionally blocked all seven `master` branches. These are the observed diagnostics to remediate, not an exemption list; rerun the exact release-pinned checker because caller workflows can change:

| Caller | Observed live blocker |
|---|---|
| `hmcts/juror-er-portal` | No `.github/workflows` directory was available to inspect. |
| `hmcts/juror-public` | The PR build inherited repository-default token permissions; CodeQL requested `security-events: write`. |
| `hmcts/juror-bureau` | The PR build inherited repository-default token permissions; CodeQL requested `security-events: write`. |
| `hmcts/juror-api` | The PR build inherited repository-default token permissions; CodeQL requested `security-events: write`. |
| `hmcts/juror-scheduler-execution` | The PR build inherited repository-default token permissions; CodeQL requested `security-events: write`. |
| `hmcts/juror-pnc` | The PR build inherited repository-default token permissions; CodeQL requested `security-events: write`. |
| `hmcts/juror-scheduler-api` | The PR build requested `id-token: write`; CodeQL requested `security-events: write`. |

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
- a repository collaborator, member or owner can invoke an exact `/codex-review` issue comment on a Codex PR, while review submissions, review comments, non-PR comments, command variants and other author associations cannot invoke it;
- the issue moves to `Peer Review` and receives `pr-ready` when a PR is created;
- runner pods contain no OpenAI or publisher credentials;
- all seven live `master` checkouts pass the exact release-pinned credential safety policy, and their normal PR checks pass;
- scheduler-api PR CI contains no Azure/ACR login, write permission, OIDC token, environment or secret-context exposure, and Azure/ACR operations are restricted to trusted non-generated branches/events;
- both caller wrappers pin the recorded, reviewed shared `main` release SHA and the post-onboarding updater dispatch completed without skipped or failed repositories; and
- every scale set returns to zero after its run.

Disable the Jira rule and set `enable_juror_runner_scale_sets = false` if routing, credential isolation, callbacks or scale-to-zero checks fail.
