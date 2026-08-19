#!/usr/bin/env ruby
# frozen_string_literal: true

require "psych"
require "yaml"

PR_EVENTS = %w[pull_request pull_request_target].freeze
SECRET_EXPRESSION = /\$\{\{.*?\bsecrets\b.*?\}\}/im.freeze

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

def pull_request_trigger?(trigger)
  case trigger
  when String
    PR_EVENTS.include?(trigger.strip)
  when Array
    trigger.any? do |event|
      unless event.is_a?(String)
        raise WorkflowSafetyError, "on sequence contains non-string event #{event.inspect}"
      end
      PR_EVENTS.include?(event.strip)
    end
  when Hash
    trigger.keys.any? { |event| PR_EVENTS.include?(event.strip) }
  else
    raise WorkflowSafetyError, "top-level on must be a string, sequence or mapping"
  end
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

def enforce_pr_policy!(workflow)
  unless workflow.key?("on")
    raise WorkflowSafetyError, "workflow is missing top-level on trigger"
  end
  return unless pull_request_trigger?(workflow["on"])

  jobs = workflow["jobs"]
  unless jobs.is_a?(Hash) && !jobs.empty?
    raise WorkflowSafetyError, "PR-triggered workflow jobs must be a non-empty mapping"
  end

  workflow_permissions = if workflow.key?("permissions")
                           parse_permissions(workflow["permissions"], "workflow.permissions")
                         end

  jobs.each do |job_name, job|
    unless job.is_a?(Hash)
      raise WorkflowSafetyError, "jobs.#{job_name} must be a mapping"
    end

    effective_writes = if job.key?("permissions")
                         parse_permissions(job["permissions"], "jobs.#{job_name}.permissions")
                       elsif workflow_permissions
                         workflow_permissions
                       else
                         raise WorkflowSafetyError,
                               "jobs.#{job_name} inherits repository-default token permissions; explicit read-only permissions are required"
                       end
    unless effective_writes.empty?
      raise WorkflowSafetyError,
            "jobs.#{job_name} has effective write permission(s): #{effective_writes.join(', ')}"
    end

    unless job.key?("uses") || job.key?("steps")
      raise WorkflowSafetyError, "jobs.#{job_name} must contain either uses or steps"
    end
    if job.key?("uses") && !job["uses"].is_a?(String)
      raise WorkflowSafetyError, "jobs.#{job_name}.uses must be a reusable-workflow reference"
    end
    if job.key?("steps") && !job["steps"].is_a?(Array)
      raise WorkflowSafetyError, "jobs.#{job_name}.steps must be a sequence"
    end
    if job.key?("uses") && job.key?("steps")
      raise WorkflowSafetyError, "jobs.#{job_name} ambiguously contains both uses and steps"
    end
    if !job.key?("uses") && job.key?("secrets")
      raise WorkflowSafetyError, "jobs.#{job_name}.secrets is only valid for a reusable-workflow call"
    end
    if job.key?("secrets") && job["secrets"].is_a?(String)
      if job["secrets"].strip == "inherit"
        raise WorkflowSafetyError, "jobs.#{job_name} passes secrets: inherit to a reusable workflow"
      end
      raise WorkflowSafetyError, "jobs.#{job_name}.secrets has unsupported scalar #{job['secrets'].inspect}"
    end
    if job.key?("secrets") && !job["secrets"].is_a?(Hash)
      raise WorkflowSafetyError, "jobs.#{job_name}.secrets must be a mapping or inherit"
    end
  end

  secret_location = find_secret_reference(workflow, "workflow")
  if secret_location
    raise WorkflowSafetyError, "#{secret_location} references the secrets context"
  end
end

repository_root = File.expand_path(Dir.pwd)
if ARGV.length == 2 && ARGV[0] == "--repository-root"
  repository_root = File.expand_path(ARGV[1])
elsif !ARGV.empty?
  warn "usage: #{$PROGRAM_NAME} [--repository-root PATH]"
  exit 2
end

workflow_dir = File.join(repository_root, ".github", "workflows")
workflow_paths = Dir.glob(File.join(workflow_dir, "*.{yml,yaml}")).sort
errors = []
workflow_paths.each do |path|
  begin
    if File.symlink?(path)
      raise WorkflowSafetyError, "workflow file must not be a symbolic link"
    end
    enforce_pr_policy!(load_workflow(path))
  rescue WorkflowSafetyError => error
    errors << "#{path.delete_prefix(repository_root + File::SEPARATOR)}: #{error.message}"
  rescue StandardError => error
    errors << "#{path.delete_prefix(repository_root + File::SEPARATOR)}: parser failure #{error.class}: #{error.message.lines.first.strip}"
  end
end

unless errors.empty?
  warn "::error title=Unsafe PR credential exposure::Autonomous Codex publication is blocked: #{errors.join('; ')}"
  exit 1
end

puts "Caller PR workflows use explicit read-only permissions and do not expose secrets."
