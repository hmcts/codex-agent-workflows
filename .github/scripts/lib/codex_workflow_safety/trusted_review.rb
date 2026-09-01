# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

def trusted_review_candidate?(workflow)
  jobs = workflow["jobs"]
  return false unless jobs.is_a?(Hash) && jobs.length == 1

  job = jobs.values.first
  job.is_a?(Hash) && job["uses"].is_a?(String) && job["uses"].start_with?(TRUSTED_REVIEW_PREFIX)
end

def validate_approved_sonar_url!(value, location)
  begin
    uri = URI.parse(value.to_s)
  rescue URI::InvalidURIError
    uri = nil
  end
  valid_origin = uri && uri.scheme == "https" && uri.host == "sonarcloud.io" &&
                 uri.userinfo.nil? && uri.port == 443 && uri.path.empty? &&
                 uri.query.nil? && uri.fragment.nil?
  unless valid_origin && value == APPROVED_SONAR_URL
    raise WorkflowSafetyError, "#{location} must equal the approved Sonar origin #{APPROVED_SONAR_URL}"
  end
end

def trusted_review_contract!(analysis)
  workflow = analysis.entry.workflow
  events = analysis.events
  unless events == {"issue_comment" => {"types" => ["created"]}}
    raise WorkflowSafetyError,
          "trusted review dispatch must use only on.issue_comment with exactly types: [created]"
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
  unless job["if"] == TRUSTED_REVIEW_IF
    raise WorkflowSafetyError,
          "#{location}.if must use the exact command and author-association gate for /codex-review on a PR comment"
  end
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
  validate_approved_sonar_url!(with["sonar_host_url"], "#{location}.with.sonar_host_url")
  with.each do |name, value|
    unless value.is_a?(String) && (!value.match?(GITHUB_EXPRESSION) || value.match?(STATIC_VAR_EXPRESSION))
      raise WorkflowSafetyError, "#{location}.with.#{name} must be a literal or static vars reference"
    end
  end

  secrets = job["secrets"]
  unless secrets.is_a?(Hash) && secrets.keys.sort == TRUSTED_REVIEW_SECRETS.sort
    raise WorkflowSafetyError, "#{location}.secrets must map exactly the three trusted review secrets"
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

  {
    "pin" => job["uses"].delete_prefix(TRUSTED_REVIEW_PREFIX),
    "with" => with
  }
end

def enforce_trusted_review_dispatch!(analysis, approved_analysis = nil)
  candidate_contract = trusted_review_contract!(analysis)
  return unless approved_analysis && approved_analysis != analysis

  approved_contract = trusted_review_contract!(approved_analysis)
  unless candidate_contract["pin"] == approved_contract["pin"]
    raise WorkflowSafetyError,
          "trusted review workflow pin must equal the immutable default-branch pin #{approved_contract['pin']}"
  end
  unless candidate_contract["with"] == approved_contract["with"]
    raise WorkflowSafetyError,
          "trusted review inputs must equal the immutable default-branch wrapper contract"
  end
end


end
