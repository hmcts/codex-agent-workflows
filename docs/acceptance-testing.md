# Juror acceptance testing

The seven Juror caller branches can be tested before merge because they share the branch name `codex/update-agent-workflows-<release>`. The shared runtime itself must first be released from the default branch and every caller must pin that immutable release SHA.

## Controlled pre-merge run

1. Confirm the shared runtime CI passes and the release commit is reachable from `main`.
2. Repin both caller workflows in all seven repositories to the release SHA.
3. Set `JIRA_JS_GITHUB_WORKFLOW_REF` to the common caller feature branch.
4. Use one dedicated documentation-only `JS-*` ticket per repository. Each ticket must have `codex-ready` and exactly one matching `codex-repo-*` label.
5. Record the Jira audit entry, workflow run, generated PR and ARC scale-set state.
6. Restore `JIRA_JS_GITHUB_WORKFLOW_REF=master` after the test window.

Never leave the Azure Function targeting a feature branch after testing.

## Evidence collection

After a run creates a PR, collect GitHub evidence with:

```bash
python3 .github/scripts/collect-acceptance-evidence.py \
  --repository hmcts/juror-api \
  --run-id 123456789 \
  --pr-number 1234 \
  --issue-key JS-1234 \
  --output acceptance-evidence/juror-api.json
```

The report records workflow and job results, runner names, retained artefacts, bot authorship, draft state, Jira initiator attribution and repository checks. Jira transitions and Kubernetes scale-to-zero remain external observations and must be added to the test record.

## Required scenarios

| Scenario | Expected result |
|---|---|
| Valid repository label | Dispatches only the selected repository and moves the issue to `In Progress` |
| Missing repository label | Azure Function rejects the request and no workflow starts |
| Multiple repository labels | Azure Function rejects the request and no workflow starts |
| Unknown repository label | Azure Function rejects the request and no workflow starts |
| Duplicate active request | Idempotency suppresses the second workflow |
| Successful verification | Bot creates a ready PR, required CI starts, Jira moves to `Peer Review` and adds `pr-ready` |
| Failed verification | Bot creates a draft PR with evidence, Jira clears running labels without adding `pr-ready` |
| No changes | No empty PR is created; Jira receives the terminal explanation |
| Implementation blocked | No empty PR is created; Jira receives the blockers and workflow link |

## Security checks

- Model-facing jobs receive the OpenAI credential only through the Codex Action proxy.
- GitHub App and Jira callback credentials are absent from planning, implementation, repair and verification jobs.
- Publishing uses the expected repository-scoped GitHub App installation.
- Generated repository workflows start automatically without a maintainer approval prompt.
- Each repository scale set starts at zero, scales to at most one runner and returns to zero.

## Default-branch limitation

GitHub evaluates `issue_comment` workflows such as `/codex-review` from the repository default branch. Run script and reusable-workflow tests before merge, but perform the final event-driven `/codex-review` smoke test after the caller workflow reaches `master`.
