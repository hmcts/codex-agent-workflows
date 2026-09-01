# frozen_string_literal: true

require "psych"
require "set"
require "uri"
require "yaml"

module CodexWorkflowSafety
  extend self

PUSH_FILTERS = %w[branches branches-ignore paths paths-ignore tags tags-ignore].freeze
WORKFLOW_RUN_FILTERS = %w[workflows types branches branches-ignore].freeze
BRANCH_REVISION_EVENTS = %w[pull_request create].freeze
OPAQUE_REVISION_EVENTS = %w[
  pull_request_target
  pull_request_review
  pull_request_review_comment
  pull_request_review_thread
  issue_comment
  merge_group
  commit_comment
  check_run
  check_suite
  status
].freeze
PROTECTED_EVENT_FILTERS = {
  "pull_request" => %w[types branches branches-ignore paths paths-ignore],
  "pull_request_target" => %w[types branches branches-ignore paths paths-ignore],
  "pull_request_review" => %w[types],
  "pull_request_review_comment" => %w[types],
  "pull_request_review_thread" => %w[types],
  "issue_comment" => %w[types],
  "merge_group" => %w[types branches branches-ignore],
  "commit_comment" => %w[types],
  "check_run" => %w[types],
  "check_suite" => %w[types],
  "create" => [],
  "status" => [],
}.freeze
TRUSTED_REVIEW_IF = "${{ github.event.issue.pull_request && github.event.comment.body == '/codex-review' && contains(fromJSON('[\"COLLABORATOR\",\"MEMBER\",\"OWNER\"]'), github.event.comment.author_association) }}"
TRUSTED_REVIEW_INPUTS = %w[
  runner_label
  node_version
  github_app_client_id
  sonar_host_url
  sonar_project_key
  required_status_context
  required_status_poll_seconds
  required_status_timeout_seconds
  jira_notify_timeout_seconds
].freeze
REQUIRED_TRUSTED_REVIEW_INPUTS = %w[
  runner_label
  github_app_client_id
  sonar_host_url
  sonar_project_key
].freeze
TRUSTED_REVIEW_SECRETS = %w[
  CODEX_OPENAI_API_KEY
  CODEX_GITHUB_APP_PRIVATE_KEY
  CODEX_JIRA_PR_NOTIFY_URL
].freeze
TRUSTED_REVIEW_PREFIX = "hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@"
TRUSTED_REVIEW_REFERENCE = %r{\Ahmcts/codex-agent-workflows/\.github/workflows/codex-review-feedback\.yml@[0-9a-f]{40}\z}.freeze
SECRET_EXPRESSION = /\$\{\{.*?\bsecrets\b.*?\}\}/im.freeze
GITHUB_EXPRESSION = /\$\{\{/.freeze
GLOB_MAGIC = /[*?\[\]{}+@]/.freeze
LOCAL_WORKFLOW = %r{\A\./\.github/workflows/([^/]+\.ya?ml)\z}.freeze
STATIC_VAR_EXPRESSION = /\A\$\{\{\s*vars\.[A-Za-z_][A-Za-z0-9_]*\s*\}\}\z/.freeze
APPROVED_SONAR_URL = "https://sonarcloud.io"

WorkflowEntry = Struct.new(:relative_path, :absolute_path, :workflow, keyword_init: true)
WorkflowRunConfig = Struct.new(:upstream_names, :branch_taint_reachable, keyword_init: true)
WorkflowAnalysis = Struct.new(
  :entry,
  :events,
  :exposures,
  :workflow_run,
  :upstreams,
  :snapshot,
  keyword_init: true
)

class WorkflowSafetyError < StandardError; end

end
