#!/usr/bin/env ruby
# frozen_string_literal: true

# Structural trust contract for this repository's own reusable workflows,
# ported from the Apps Reg workflow trust checker. It is a regression guard:
# the callers' GitHub environments and runner-group policies remain the
# enforced boundary.

require "json"
require_relative "lib/codex_workflow_safety/types"
require_relative "lib/codex_workflow_safety/yaml_loader"

MODEL_ACTION = "openai/codex-action@"
HOSTED_RUNNER = "ubuntu-latest"
MODEL_RUNNER = {
  "group" => "${{ inputs.runner_group }}",
  "labels" => "${{ inputs.runner_label }}",
}.freeze
MODEL_ENVIRONMENT = "${{ inputs.model_environment }}"
PUBLISHER_ENVIRONMENT = "${{ inputs.publisher_environment }}"
# Sonar-only jobs read analysis results and are deliberately not gated by an
# environment, so CODEX_SONAR_TOKEN maps to no environment.
SECRET_ENVIRONMENTS = {
  "CODEX_OPENAI_API_KEY" => MODEL_ENVIRONMENT,
  "CODEX_GITHUB_APP_PRIVATE_KEY" => PUBLISHER_ENVIRONMENT,
  "CODEX_JIRA_PR_NOTIFY_URL" => PUBLISHER_ENVIRONMENT,
  "CODEX_SONAR_TOKEN" => nil,
}.freeze
FORWARDED_INPUTS = %w[runner_group runner_label model_environment publisher_environment].freeze
LOCAL_WORKFLOW = %r{\A\./\.github/workflows/(?<name>[A-Za-z0-9_.-]+\.ya?ml)\z}

def declared_inputs(workflow)
  workflow.dig("on", "workflow_call", "inputs")&.keys || []
end

def check_call_site(filename, name, job, workflows, errors)
  match = LOCAL_WORKFLOW.match(job["uses"].to_s)
  unless match
    errors << "#{filename}:#{name}: call sites must use this repository's workflows"
    return
  end
  callee = workflows[match[:name]]
  unless callee
    errors << "#{filename}:#{name}: calls a workflow that does not exist: #{match[:name]}"
    return
  end
  with = job.fetch("with", {}) || {}
  (FORWARDED_INPUTS & declared_inputs(callee)).each do |input|
    unless with[input] == "${{ inputs.#{input} }}"
      errors << "#{filename}:#{name}: must forward #{input} unchanged to #{match[:name]}"
    end
  end
end

def check_job(filename, name, job, errors)
  steps = job.fetch("steps", []) || []
  model_index = steps.index { |step| step.fetch("uses", "").to_s.start_with?(MODEL_ACTION) }
  secrets = JSON.generate(job).scan(/secrets\.([A-Z_][A-Z0-9_]*)/).flatten.uniq - ["GITHUB_TOKEN"]

  if model_index
    unless job["runs-on"] == MODEL_RUNNER
      errors << "#{filename}:#{name}: model execution requires the caller's runner group and label"
    end
    unless job["environment"] == MODEL_ENVIRONMENT
      errors << "#{filename}:#{name}: model jobs must run in the caller's model environment"
    end
    unless secrets == ["CODEX_OPENAI_API_KEY"]
      errors << "#{filename}:#{name}: model jobs may receive only the model API key"
    end
    unless model_index == steps.length - 1
      errors << "#{filename}:#{name}: the Codex Action must be the final step of its model job"
    end
    return
  end

  unless job["runs-on"] == HOSTED_RUNNER
    errors << "#{filename}:#{name}: non-model jobs must use GitHub-hosted compute"
  end
  if secrets.include?("CODEX_OPENAI_API_KEY")
    errors << "#{filename}:#{name}: only model jobs may receive the model API key"
    return
  end
  environments = secrets.map { |secret| SECRET_ENVIRONMENTS.fetch(secret) }.compact.uniq
  if environments.empty?
    if job.key?("environment")
      errors << "#{filename}:#{name}: credential-free jobs must not declare an environment"
    end
  elsif job["environment"] != PUBLISHER_ENVIRONMENT
    errors << "#{filename}:#{name}: publisher credentials require the caller's publisher environment"
  end
end

def check_workflow(filename, workflow, workflows, errors)
  triggers = workflow["on"]
  unless triggers.is_a?(Hash) && triggers.keys == ["workflow_call"]
    errors << "#{filename}: only workflow_call may start this workflow"
  end

  (workflow["jobs"] || {}).each do |name, job|
    encoded = JSON.generate(job)
    referenced = encoded.scan(/secrets\.([A-Z_][A-Z0-9_]*)/).flatten.uniq - ["GITHUB_TOKEN"]
    if job["secrets"] == "inherit"
      errors << "#{filename}:#{name}: jobs must not inherit every caller secret"
    end
    if encoded.match?(/secrets\s*\[/) || (referenced - SECRET_ENVIRONMENTS.keys).any?
      errors << "#{filename}:#{name}: unknown or dynamic credential reference"
      next
    end

    permissions = job["permissions"]
    unless permissions == {} ||
           (permissions.is_a?(Hash) && permissions.values.all? { |value| %w[read none].include?(value) })
      errors << "#{filename}:#{name}: job permissions must be explicit and read-only"
    end

    if job.key?("uses")
      if job.key?("environment")
        errors << "#{filename}:#{name}: call sites cannot declare an environment"
      end
      check_call_site(filename, name, job, workflows, errors)
    else
      check_job(filename, name, job, errors)
    end
  end
end

root = ARGV.fetch(0, ".")
paths = Dir.glob(File.join(root, ".github", "workflows", "codex-*.yml")).sort
abort "no codex-*.yml workflows found under #{root}" if paths.empty?

errors = []
workflows = {}
paths.each do |path|
  workflows[File.basename(path)] = CodexWorkflowSafety.load_workflow(path)
rescue CodexWorkflowSafety::WorkflowSafetyError => error
  errors << "#{File.basename(path)}: #{error.message}"
end
workflows.each { |filename, workflow| check_workflow(filename, workflow, workflows, errors) }

abort errors.join("\n") unless errors.empty?
puts "Shared workflow trust contract passed for #{workflows.size} workflows."
