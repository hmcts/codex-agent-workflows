# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

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


end
