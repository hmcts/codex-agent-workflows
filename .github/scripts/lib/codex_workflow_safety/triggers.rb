# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

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


end
