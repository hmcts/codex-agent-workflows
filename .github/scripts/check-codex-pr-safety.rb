#!/usr/bin/env ruby
# frozen_string_literal: true

require "psych"
require "set"
require "yaml"

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
TRUSTED_REVIEW_EVENTS = {
  "pull_request_review" => ["submitted"],
  "pull_request_review_comment" => ["created"],
  "issue_comment" => ["created"],
}.freeze
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
  CODEX_SONAR_TOKEN
].freeze
TRUSTED_REVIEW_PREFIX = "hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@"
TRUSTED_REVIEW_REFERENCE = %r{\Ahmcts/codex-agent-workflows/\.github/workflows/codex-review-feedback\.yml@[0-9a-f]{40}\z}.freeze
SECRET_EXPRESSION = /\$\{\{.*?\bsecrets\b.*?\}\}/im.freeze
GITHUB_EXPRESSION = /\$\{\{/.freeze
GLOB_MAGIC = /[*?\[\]{}+@]/.freeze
LOCAL_WORKFLOW = %r{\A\./\.github/workflows/([^/]+\.ya?ml)\z}.freeze
STATIC_VAR_EXPRESSION = /\A\$\{\{\s*vars\.[A-Za-z_][A-Za-z0-9_]*\s*\}\}\z/.freeze

WorkflowEntry = Struct.new(:relative_path, :absolute_path, :workflow, keyword_init: true)
WorkflowRunConfig = Struct.new(:upstream_names, :branch_taint_reachable, keyword_init: true)
WorkflowAnalysis = Struct.new(
  :entry,
  :events,
  :exposures,
  :workflow_run,
  :upstreams,
  keyword_init: true
)

class WorkflowSafetyError < StandardError; end

def validate_ast!(node, location, top_level = false)
  case node
  when Psych::Nodes::Mapping
    seen_keys = {}
    node.children.each_slice(2) do |key_node, value_node|
      unless key_node.is_a?(Psych::Nodes::Scalar)
        raise WorkflowSafetyError, "#{location} contains a non-scalar mapping key"
      end

      key = key_node.value
      if key == "<<"
        raise WorkflowSafetyError, "#{location} uses a YAML merge key, whose effective policy is ambiguous"
      end
      if seen_keys.key?(key)
        raise WorkflowSafetyError, "#{location} contains duplicate key #{key.inspect}"
      end
      seen_keys[key] = true

      yaml_boolean_key = %w[true false yes no on off y n].include?(key.downcase)
      if top_level && key_node.plain && yaml_boolean_key && key != "on"
        raise WorkflowSafetyError, "top-level key #{key.inspect} is ambiguous under GitHub's special on semantics"
      end
      validate_ast!(value_node, "#{location}.#{key}")
    end
  when Psych::Nodes::Sequence
    node.children.each_with_index do |child, index|
      validate_ast!(child, "#{location}[#{index}]")
    end
  when Psych::Nodes::Scalar, Psych::Nodes::Alias
    nil
  else
    raise WorkflowSafetyError, "#{location} contains unsupported YAML node #{node.class}"
  end
end

def validate_string_keys!(value, location, visited = {})
  return if value.nil? || value.is_a?(String) || value == true || value == false || value.is_a?(Numeric)
  return if visited[value.object_id]

  visited[value.object_id] = true
  case value
  when Hash
    value.each do |key, child|
      unless key.is_a?(String)
        raise WorkflowSafetyError, "#{location} contains non-string key #{key.inspect}"
      end
      validate_string_keys!(child, "#{location}.#{key}", visited)
    end
  when Array
    value.each_with_index do |child, index|
      validate_string_keys!(child, "#{location}[#{index}]", visited)
    end
  else
    raise WorkflowSafetyError, "#{location} contains unsupported value #{value.class}"
  end
end

def load_workflow(path)
  source = File.read(path, encoding: "UTF-8")
  stream = Psych.parse_stream(source, filename: path.to_s)
  unless stream.children.length == 1
    raise WorkflowSafetyError, "workflow must contain exactly one YAML document"
  end

  document = stream.children.first
  root = document.root
  unless root.is_a?(Psych::Nodes::Mapping)
    raise WorkflowSafetyError, "workflow root must be a mapping"
  end
  validate_ast!(root, "workflow", true)

  begin
    workflow = YAML.safe_load(
      source,
      permitted_classes: [],
      permitted_symbols: [],
      aliases: true,
      filename: path.to_s
    )
  rescue Psych::Exception => error
    raise WorkflowSafetyError, "YAML could not be resolved safely: #{error.message.lines.first.strip}"
  end
  unless workflow.is_a?(Hash)
    raise WorkflowSafetyError, "workflow root did not resolve to a mapping"
  end

  raw_on_key = root.children.each_slice(2).find do |key_node, _value_node|
    key_node.is_a?(Psych::Nodes::Scalar) && key_node.value == "on"
  end
  if raw_on_key && !workflow.key?("on")
    unless workflow.key?(true)
      raise WorkflowSafetyError, "top-level on trigger could not be resolved"
    end
    workflow["on"] = workflow.delete(true)
  end

  validate_string_keys!(workflow, "workflow")
  workflow
rescue Psych::SyntaxError => error
  raise WorkflowSafetyError, "malformed YAML: #{error.message.lines.first.strip}"
end

def trigger_events(trigger)
  raw_events = case trigger
               when String
                 [[trigger, nil]]
               when Array
                 trigger.map.with_index do |event, index|
                   unless event.is_a?(String)
                     raise WorkflowSafetyError, "on[#{index}] contains non-string event #{event.inspect}"
                   end
                   [event, nil]
                 end
               when Hash
                 trigger.to_a
               else
                 raise WorkflowSafetyError, "top-level on must be a string, sequence or mapping"
               end

  raw_events.each_with_object({}) do |(raw_event, configuration), events|
    event = raw_event.strip
    if event.empty? || event.match?(GITHUB_EXPRESSION)
      raise WorkflowSafetyError, "top-level on contains a dynamic or empty event name"
    end
    if events.key?(event)
      raise WorkflowSafetyError, "top-level on contains duplicate event #{event.inspect}"
    end
    events[event] = configuration
  end
end

def static_patterns(configuration, key, location)
  return nil unless configuration.key?(key)

  patterns = configuration[key]
  unless patterns.is_a?(Array) && !patterns.empty?
    raise WorkflowSafetyError, "#{location}.#{key} must be a non-empty sequence"
  end
  patterns.map.with_index do |pattern, index|
    unless pattern.is_a?(String) && !pattern.empty?
      raise WorkflowSafetyError, "#{location}.#{key}[#{index}] must be a non-empty string"
    end
    if pattern.match?(GITHUB_EXPRESSION) || pattern.include?("\\")
      raise WorkflowSafetyError, "#{location}.#{key}[#{index}] is dynamic or ambiguous"
    end
    pattern
  end
end

def pattern_may_match_generated_branch?(pattern)
  magic_index = pattern.index(GLOB_MAGIC)
  return pattern.start_with?("codex/") unless magic_index

  literal_prefix = pattern[0...magic_index]
  literal_prefix.empty? || "codex/".start_with?(literal_prefix) || literal_prefix.start_with?("codex/")
end

def excludes_every_generated_branch?(pattern)
  %w[** codex/**].include?(pattern)
end

def branches_may_match_generated?(patterns, location)
  may_match = false
  saw_positive = false

  patterns.each.with_index do |pattern, index|
    negative = pattern.start_with?("!")
    body = negative ? pattern[1..] : pattern
    if body.empty? || body.include?("!")
      raise WorkflowSafetyError, "#{location}[#{index}] has ambiguous negation"
    end

    if negative
      may_match = false if excludes_every_generated_branch?(body)
    else
      saw_positive = true
      may_match = true if pattern_may_match_generated_branch?(body)
    end
  end

  unless saw_positive
    raise WorkflowSafetyError, "#{location} must contain at least one positive pattern"
  end
  may_match
end

def branch_filters_may_match_generated?(configuration, location)
  branches = static_patterns(configuration, "branches", location)
  branches_ignore = static_patterns(configuration, "branches-ignore", location)
  if branches && branches_ignore
    raise WorkflowSafetyError, "#{location} cannot combine branches and branches-ignore"
  end

  return branches_may_match_generated?(branches, "#{location}.branches") if branches
  if branches_ignore
    branches_ignore.each.with_index do |pattern, index|
      if pattern.start_with?("!") || pattern.include?("!")
        raise WorkflowSafetyError, "#{location}.branches-ignore[#{index}] has ambiguous negation"
      end
    end
    return !branches_ignore.any? { |pattern| excludes_every_generated_branch?(pattern) }
  end

  true
end

def push_may_run_generated_branch?(configuration)
  return true if configuration.nil?
  unless configuration.is_a?(Hash)
    raise WorkflowSafetyError, "on.push must be empty or a filter mapping"
  end

  unsupported = configuration.keys - PUSH_FILTERS
  unless unsupported.empty?
    raise WorkflowSafetyError, "on.push contains unsupported filter(s): #{unsupported.join(', ')}"
  end

  PUSH_FILTERS.each { |key| static_patterns(configuration, key, "on.push") }
  if configuration.key?("tags") && configuration.key?("tags-ignore")
    raise WorkflowSafetyError, "on.push cannot combine tags and tags-ignore"
  end
  if configuration.key?("paths") && configuration.key?("paths-ignore")
    raise WorkflowSafetyError, "on.push cannot combine paths and paths-ignore"
  end

  has_branch_filters = configuration.key?("branches") || configuration.key?("branches-ignore")
  return branch_filters_may_match_generated?(configuration, "on.push") if has_branch_filters

  # GitHub suppresses branch pushes when only tag filters are configured.
  return false if configuration.key?("tags") || configuration.key?("tags-ignore")

  true
end

def validate_protected_event_configuration!(event, configuration)
  return if configuration.nil?
  unless configuration.is_a?(Hash)
    raise WorkflowSafetyError, "on.#{event} must be empty or a filter mapping"
  end

  allowed = PROTECTED_EVENT_FILTERS.fetch(event)
  unsupported = configuration.keys - allowed
  unless unsupported.empty?
    raise WorkflowSafetyError, "on.#{event} contains unsupported filter(s): #{unsupported.join(', ')}"
  end
  allowed.each { |key| static_patterns(configuration, key, "on.#{event}") }
  if configuration.key?("branches") && configuration.key?("branches-ignore")
    raise WorkflowSafetyError, "on.#{event} cannot combine branches and branches-ignore"
  end
  if configuration.key?("paths") && configuration.key?("paths-ignore")
    raise WorkflowSafetyError, "on.#{event} cannot combine paths and paths-ignore"
  end
end

def parse_workflow_run(configuration)
  unless configuration.is_a?(Hash)
    raise WorkflowSafetyError, "on.workflow_run must be an explicit filter mapping"
  end

  unsupported = configuration.keys - WORKFLOW_RUN_FILTERS
  unless unsupported.empty?
    raise WorkflowSafetyError, "on.workflow_run contains unsupported filter(s): #{unsupported.join(', ')}"
  end

  upstream_names = static_patterns(configuration, "workflows", "on.workflow_run")
  unless upstream_names
    raise WorkflowSafetyError, "on.workflow_run.workflows must explicitly name every upstream workflow"
  end
  if upstream_names.uniq.length != upstream_names.length
    raise WorkflowSafetyError, "on.workflow_run.workflows contains duplicate upstream names"
  end
  static_patterns(configuration, "types", "on.workflow_run") if configuration.key?("types")

  WorkflowRunConfig.new(
    upstream_names: upstream_names,
    branch_taint_reachable: branch_filters_may_match_generated?(configuration, "on.workflow_run")
  )
end

def workflow_call_trigger?(workflow)
  return false unless workflow.key?("on")

  events = trigger_events(workflow["on"])
  return false unless events.key?("workflow_call")

  configuration = events["workflow_call"]
  unless configuration.nil? || configuration.is_a?(Hash)
    raise WorkflowSafetyError, "on.workflow_call must be empty or a mapping"
  end
  true
end

def workflow_name!(entry)
  name = entry.workflow["name"]
  unless name.is_a?(String) && !name.strip.empty? && !name.match?(GITHUB_EXPRESSION)
    raise WorkflowSafetyError,
          "#{entry.relative_path} must declare a static non-empty name for workflow_run resolution"
  end
  name.strip
end

def detect_workflow_run_cycles!(analyses)
  states = {}
  stack = []
  visit = lambda do |analysis|
    path = analysis.entry.relative_path
    if states[path] == :visiting
      cycle_start = stack.index(path) || 0
      cycle = (stack[cycle_start..] + [path]).join(" -> ")
      raise WorkflowSafetyError, "workflow_run cycle detected: #{cycle}"
    end
    return if states[path] == :visited

    states[path] = :visiting
    stack << path
    analysis.upstreams.each { |upstream| visit.call(upstream) } if analysis.workflow_run
    stack.pop
    states[path] = :visited
  end

  analyses.each_value { |analysis| visit.call(analysis) }
end

def analyze_workflow_exposure(entries)
  analyses = {}
  entries.each_value do |entry|
    begin
      unless entry.workflow.key?("on")
        raise WorkflowSafetyError, "workflow is missing top-level on trigger"
      end
      events = trigger_events(entry.workflow["on"])
      exposures = Set.new

      if events.key?("push") && push_may_run_generated_branch?(events["push"])
        exposures << :branch
      end
      BRANCH_REVISION_EVENTS.each do |event|
        next unless events.key?(event)

        validate_protected_event_configuration!(event, events[event])
        exposures << :branch
      end
      OPAQUE_REVISION_EVENTS.each do |event|
        next unless events.key?(event)

        validate_protected_event_configuration!(event, events[event])
        exposures << :opaque
      end

      workflow_run = parse_workflow_run(events["workflow_run"]) if events.key?("workflow_run")
      analyses[entry.relative_path] = WorkflowAnalysis.new(
        entry: entry,
        events: events,
        exposures: exposures,
        workflow_run: workflow_run,
        upstreams: []
      )
    rescue WorkflowSafetyError => error
      raise WorkflowSafetyError, "#{entry.relative_path}: #{error.message}"
    end
  end

  listeners = analyses.values.select(&:workflow_run)
  unless listeners.empty?
    names = {}
    analyses.each_value do |analysis|
      name = workflow_name!(analysis.entry)
      if names.key?(name)
        raise WorkflowSafetyError,
              "workflow name #{name.inspect} is ambiguous between #{names[name].entry.relative_path} and #{analysis.entry.relative_path}"
      end
      names[name] = analysis
    end

    listeners.each do |listener|
      listener.upstreams = listener.workflow_run.upstream_names.map do |name|
        upstream = names[name]
        unless upstream
          raise WorkflowSafetyError,
                "#{listener.entry.relative_path}: on.workflow_run references missing upstream workflow #{name.inspect}"
        end
        upstream
      end
    end

    detect_workflow_run_cycles!(analyses)

    changed = true
    while changed
      changed = false
      listeners.each do |listener|
        listener.upstreams.each do |upstream|
          if upstream.exposures.include?(:opaque) && !listener.exposures.include?(:opaque)
            listener.exposures << :opaque
            changed = true
          end
          if listener.workflow_run.branch_taint_reachable && upstream.exposures.include?(:branch) &&
             !listener.exposures.include?(:branch)
            listener.exposures << :branch
            changed = true
          end
        end
      end
    end
  end

  analyses
end

def parse_permissions(value, location)
  case value
  when String
    permission = value.strip
    return [] if permission == "read-all"
    return ["write-all"] if permission == "write-all"

    raise WorkflowSafetyError, "#{location} has unsupported scalar #{value.inspect}"
  when Hash
    value.each_with_object([]) do |(permission, access), writes|
      unless access.is_a?(String)
        raise WorkflowSafetyError, "#{location}.#{permission} must be read, write or none"
      end
      normalized_access = access.strip
      unless %w[read write none].include?(normalized_access)
        raise WorkflowSafetyError, "#{location}.#{permission} has unsupported access #{access.inspect}"
      end
      writes << permission if normalized_access == "write"
    end
  else
    raise WorkflowSafetyError, "#{location} must be read-all, write-all or a permission mapping"
  end
end

def find_secret_reference(value, location, visited = {})
  if value.is_a?(String)
    return location if value.match?(SECRET_EXPRESSION)
    return nil
  end
  return nil unless value.is_a?(Hash) || value.is_a?(Array)
  return nil if visited[value.object_id]

  visited[value.object_id] = true
  if value.is_a?(Hash)
    value.each do |key, child|
      return "#{location}.#{key}" if key.match?(SECRET_EXPRESSION)
      found = find_secret_reference(child, "#{location}.#{key}", visited)
      return found if found
    end
  else
    value.each_with_index do |child, index|
      found = find_secret_reference(child, "#{location}[#{index}]", visited)
      return found if found
    end
  end
  nil
end

def validate_reusable_secrets!(job, location)
  return unless job.key?("secrets")

  secrets = job["secrets"]
  if secrets.is_a?(String)
    if secrets.strip == "inherit"
      raise WorkflowSafetyError, "#{location}.secrets passes secrets: inherit to a reusable workflow"
    end
    raise WorkflowSafetyError, "#{location}.secrets has unsupported scalar #{secrets.inspect}"
  end
  unless secrets.is_a?(Hash)
    raise WorkflowSafetyError, "#{location}.secrets must be a mapping or inherit"
  end
  unless secrets.empty?
    raise WorkflowSafetyError, "#{location}.secrets passes credentials to a reusable workflow"
  end
end

def resolve_local_workflow!(uses, entries, location)
  unless uses.is_a?(String)
    raise WorkflowSafetyError, "#{location}.uses must be a literal local reusable-workflow reference"
  end
  if uses.match?(GITHUB_EXPRESSION)
    raise WorkflowSafetyError, "#{location}.uses is dynamic or ambiguous"
  end

  match = LOCAL_WORKFLOW.match(uses)
  unless match
    raise WorkflowSafetyError, "#{location}.uses references an external or unsupported reusable workflow #{uses.inspect}"
  end

  relative_path = File.join(".github", "workflows", match[1])
  target = entries[relative_path]
  unless target
    raise WorkflowSafetyError, "#{location}.uses references missing local workflow #{relative_path}"
  end
  unless workflow_call_trigger?(target.workflow)
    raise WorkflowSafetyError, "#{location}.uses target #{relative_path} is missing an on.workflow_call trigger"
  end
  target
end

def effective_job_writes!(job, job_location, workflow_permissions)
  if job.key?("permissions")
    parse_permissions(job["permissions"], "#{job_location}.permissions")
  elsif workflow_permissions
    workflow_permissions
  else
    raise WorkflowSafetyError,
          "#{job_location} inherits repository-default token permissions; explicit read-only permissions are required"
  end
end

def enforce_policy!(entry, entries, inherited_permissions = nil, stack = [])
  if stack.include?(entry.relative_path)
    cycle = (stack + [entry.relative_path]).join(" -> ")
    raise WorkflowSafetyError, "reusable workflow cycle detected: #{cycle}"
  end
  stack = stack + [entry.relative_path]
  workflow = entry.workflow

  secret_location = find_secret_reference(workflow, "workflow")
  if secret_location
    raise WorkflowSafetyError, "#{secret_location} references the secrets context"
  end

  jobs = workflow["jobs"]
  unless jobs.is_a?(Hash) && !jobs.empty?
    raise WorkflowSafetyError, "protected workflow jobs must be a non-empty mapping"
  end

  workflow_permissions = if workflow.key?("permissions")
                           parse_permissions(workflow["permissions"], "workflow.permissions")
                         else
                           inherited_permissions
                         end

  jobs.each do |job_name, job|
    location = "jobs.#{job_name}"
    unless job.is_a?(Hash)
      raise WorkflowSafetyError, "#{location} must be a mapping"
    end
    if job.key?("environment")
      raise WorkflowSafetyError, "#{location}.environment can expose environment-backed credentials"
    end

    effective_writes = effective_job_writes!(job, location, workflow_permissions)
    unless effective_writes.empty?
      raise WorkflowSafetyError,
            "#{location} has effective write permission(s): #{effective_writes.join(', ')}"
    end

    has_uses = job.key?("uses")
    has_steps = job.key?("steps")
    unless has_uses || has_steps
      raise WorkflowSafetyError, "#{location} must contain either uses or steps"
    end
    if has_uses && has_steps
      raise WorkflowSafetyError, "#{location} ambiguously contains both uses and steps"
    end

    if has_uses
      validate_reusable_secrets!(job, location)
      target = resolve_local_workflow!(job["uses"], entries, location)
      enforce_policy!(target, entries, effective_writes, stack)
    else
      unless job["steps"].is_a?(Array)
        raise WorkflowSafetyError, "#{location}.steps must be a sequence"
      end
      if job.key?("secrets")
        raise WorkflowSafetyError, "#{location}.secrets is only valid for a reusable-workflow call"
      end
    end
  end
end

def trusted_review_candidate?(workflow)
  jobs = workflow["jobs"]
  return false unless jobs.is_a?(Hash) && jobs.length == 1

  job = jobs.values.first
  job.is_a?(Hash) && job["uses"].is_a?(String) && job["uses"].start_with?(TRUSTED_REVIEW_PREFIX)
end

def enforce_trusted_review_dispatch!(analysis)
  workflow = analysis.entry.workflow
  events = analysis.events
  unless !events.empty? && (events.keys - TRUSTED_REVIEW_EVENTS.keys).empty?
    raise WorkflowSafetyError,
          "trusted review dispatch may use only issue_comment, pull_request_review and pull_request_review_comment"
  end
  events.each do |event, configuration|
    expected = {"types" => TRUSTED_REVIEW_EVENTS.fetch(event)}
    unless configuration == expected
      raise WorkflowSafetyError,
            "on.#{event} must use exactly types: [#{TRUSTED_REVIEW_EVENTS.fetch(event).join(', ')}] for trusted review dispatch"
    end
  end

  top_level = workflow.reject { |key, _value| key == "jobs" }
  secret_location = find_secret_reference(top_level, "workflow")
  if secret_location
    raise WorkflowSafetyError, "#{secret_location} references the secrets context outside the trusted mapping"
  end
  if workflow.key?("env")
    raise WorkflowSafetyError, "trusted review dispatch must not define workflow-level env"
  end

  unless workflow.key?("permissions")
    raise WorkflowSafetyError, "trusted review dispatch requires explicit read-only workflow permissions"
  end
  workflow_permissions = parse_permissions(workflow["permissions"], "workflow.permissions")

  job_name, job = workflow["jobs"].first
  location = "jobs.#{job_name}"
  if job.key?("environment")
    raise WorkflowSafetyError, "#{location}.environment can expose environment-backed credentials"
  end
  if job.key?("steps")
    raise WorkflowSafetyError, "#{location} must not execute steps in trusted review dispatch"
  end

  effective_writes = effective_job_writes!(job, location, workflow_permissions)
  unless effective_writes.empty?
    raise WorkflowSafetyError,
          "#{location} has effective write permission(s): #{effective_writes.join(', ')}"
  end

  unless job["uses"].match?(TRUSTED_REVIEW_REFERENCE)
    raise WorkflowSafetyError, "#{location}.uses must pin the trusted HMCTS review workflow to a 40-character SHA"
  end

  with = job["with"]
  unless with.is_a?(Hash)
    raise WorkflowSafetyError, "#{location}.with must be an explicit input mapping"
  end
  unsupported_inputs = with.keys - TRUSTED_REVIEW_INPUTS
  unless unsupported_inputs.empty?
    raise WorkflowSafetyError, "#{location}.with contains unsupported input(s): #{unsupported_inputs.join(', ')}"
  end
  missing_inputs = REQUIRED_TRUSTED_REVIEW_INPUTS - with.keys
  unless missing_inputs.empty?
    raise WorkflowSafetyError, "#{location}.with is missing required input(s): #{missing_inputs.join(', ')}"
  end
  with.each do |name, value|
    unless value.is_a?(String) && (!value.match?(GITHUB_EXPRESSION) || value.match?(STATIC_VAR_EXPRESSION))
      raise WorkflowSafetyError, "#{location}.with.#{name} must be a literal or static vars reference"
    end
  end

  secrets = job["secrets"]
  unless secrets.is_a?(Hash) && secrets.keys.sort == TRUSTED_REVIEW_SECRETS.sort
    raise WorkflowSafetyError, "#{location}.secrets must map exactly the four trusted review secrets"
  end
  TRUSTED_REVIEW_SECRETS.each do |name|
    expected = "${{ secrets.#{name} }}"
    unless secrets[name] == expected
      raise WorkflowSafetyError, "#{location}.secrets.#{name} must map exactly to #{expected}"
    end
  end

  job_without_secrets = job.reject { |key, _value| key == "secrets" }
  secret_location = find_secret_reference(job_without_secrets, location)
  if secret_location
    raise WorkflowSafetyError, "#{secret_location} references the secrets context outside the trusted mapping"
  end
end

def discover_workflows(repository_root)
  workflow_dir = File.join(repository_root, ".github", "workflows")
  unless File.exist?(workflow_dir)
    raise WorkflowSafetyError, ".github/workflows does not exist"
  end
  if File.symlink?(workflow_dir) || !File.directory?(workflow_dir)
    raise WorkflowSafetyError, ".github/workflows must be a real directory"
  end

  names = Dir.children(workflow_dir).sort
  raise WorkflowSafetyError, ".github/workflows contains no workflow entries" if names.empty?

  entries = {}
  errors = []
  names.each do |name|
    path = File.join(workflow_dir, name)
    relative_path = File.join(".github", "workflows", name)
    begin
      stat = File.lstat(path)
      if stat.symlink?
        raise WorkflowSafetyError, "workflow entry must not be a symbolic link"
      end
      unless stat.file?
        raise WorkflowSafetyError, "workflow entry must be a regular file"
      end
      unless name.end_with?(".yml", ".yaml")
        raise WorkflowSafetyError, "workflow entry has unsupported extension"
      end

      entries[relative_path] = WorkflowEntry.new(
        relative_path: relative_path,
        absolute_path: path,
        workflow: load_workflow(path)
      )
    rescue WorkflowSafetyError => error
      errors << "#{relative_path}: #{error.message}"
    rescue StandardError => error
      errors << "#{relative_path}: parser failure #{error.class}: #{error.message.lines.first.strip}"
    end
  end
  [entries, errors]
end

repository_root = File.expand_path(Dir.pwd)
if ARGV.length == 2 && ARGV[0] == "--repository-root"
  repository_root = File.expand_path(ARGV[1])
elsif !ARGV.empty?
  warn "usage: #{$PROGRAM_NAME} [--repository-root PATH]"
  exit 2
end

errors = []
entries = {}
begin
  entries, discovery_errors = discover_workflows(repository_root)
  errors.concat(discovery_errors)
rescue WorkflowSafetyError => error
  errors << ".github/workflows: #{error.message}"
rescue StandardError => error
  errors << ".github/workflows: parser failure #{error.class}: #{error.message.lines.first.strip}"
end

analyses = {}
begin
  analyses = analyze_workflow_exposure(entries)
rescue WorkflowSafetyError => error
  errors << error.message
rescue StandardError => error
  errors << ".github/workflows: graph analysis failure #{error.class}: #{error.message.lines.first.strip}"
end

analyses.each_value do |analysis|
  next if analysis.exposures.empty?

  begin
    if trusted_review_candidate?(analysis.entry.workflow)
      enforce_trusted_review_dispatch!(analysis)
    else
      enforce_policy!(analysis.entry, entries)
    end
  rescue WorkflowSafetyError => error
    errors << "#{analysis.entry.relative_path}: #{error.message}"
  rescue StandardError => error
    errors << "#{analysis.entry.relative_path}: parser failure #{error.class}: #{error.message.lines.first.strip}"
  end
end

unless errors.empty?
  warn "::error title=Unsafe generated-code credential exposure::Autonomous Codex publication is blocked: #{errors.join('; ')}"
  exit 1
end

puts "Caller workflows reachable from untrusted revisions or downstream workflow_run chains are explicitly isolated."
