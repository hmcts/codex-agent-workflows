# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

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
    key = analysis.object_id
    label = "#{analysis.snapshot}:#{analysis.entry.relative_path}"
    if states[key] == :visiting
      cycle_start = stack.index(label) || 0
      cycle = (stack[cycle_start..] + [label]).join(" -> ")
      raise WorkflowSafetyError, "workflow_run cycle detected: #{cycle}"
    end
    return if states[key] == :visited

    states[key] = :visiting
    stack << label
    analysis.upstreams.each { |upstream| visit.call(upstream) } if analysis.workflow_run
    stack.pop
    states[key] = :visited
  end

  analyses.each_value { |analysis| visit.call(analysis) }
end

def parse_workflow_analyses(entries, snapshot)
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
        upstreams: [],
        snapshot: snapshot
      )
    rescue WorkflowSafetyError => error
      raise WorkflowSafetyError, "#{entry.relative_path}: #{error.message}"
    end
  end

  analyses
end

def workflow_name_map!(analyses, snapshot)
  names = {}
  analyses.each_value do |analysis|
    name = workflow_name!(analysis.entry)
    if names.key?(name)
      raise WorkflowSafetyError,
            "#{snapshot} workflow name #{name.inspect} is ambiguous between #{names[name].entry.relative_path} and #{analysis.entry.relative_path}"
    end
    names[name] = analysis
  end
  names
end

def resolve_snapshot_graph!(analyses, names)
  analyses.values.select(&:workflow_run).each do |listener|
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
end

def propagate_snapshot_exposure!(analyses)
  listeners = analyses.values.select(&:workflow_run)
  changed = true
  while changed
    changed = false
    listeners.each do |listener|
      reachable = listener.upstreams.any? do |upstream|
        upstream.exposures.include?(:opaque) ||
          (listener.workflow_run.branch_taint_reachable && upstream.exposures.include?(:branch))
      end
      next unless reachable && !listener.exposures.include?(:opaque)

      # A workflow_run listener loads its definition from the default branch. Later
      # listeners cannot safely use branch filters to erase this transitive exposure.
      listener.exposures << :opaque
      changed = true
    end
  end
end

def analyze_two_tree_exposure(candidate_entries, trusted_entries)
  candidate = parse_workflow_analyses(candidate_entries, :candidate)
  trusted = parse_workflow_analyses(trusted_entries, :trusted)
  all_listeners = candidate.values.select(&:workflow_run) + trusted.values.select(&:workflow_run)
  return [candidate, trusted, trusted.transform_values { |analysis| analysis.exposures.dup }] if all_listeners.empty?

  candidate_names = workflow_name_map!(candidate, :candidate)
  trusted_names = workflow_name_map!(trusted, :trusted)

  resolve_snapshot_graph!(candidate, candidate_names)
  propagate_snapshot_exposure!(candidate)

  trusted.values.select(&:workflow_run).each do |listener|
    listener.workflow_run.upstream_names.each do |name|
      unless candidate_names.key?(name) || trusted_names.key?(name)
        raise WorkflowSafetyError,
              "#{listener.entry.relative_path}: on.workflow_run references missing candidate/trusted upstream workflow #{name.inspect}"
      end
    end
    listener.upstreams = listener.workflow_run.upstream_names.map { |name| trusted_names[name] }.compact
  end
  detect_workflow_run_cycles!(trusted)

  cross_exposures = trusted.transform_values { |analysis| analysis.exposures.dup }
  listeners = trusted.values.select(&:workflow_run)
  changed = true
  while changed
    changed = false
    listeners.each do |listener|
      reachable = listener.workflow_run.upstream_names.any? do |name|
        candidate_source = candidate_names[name]&.exposures || Set.new
        trusted_source = trusted_names[name] ? cross_exposures.fetch(trusted_names[name].entry.relative_path) : Set.new
        candidate_source.include?(:opaque) || trusted_source.include?(:opaque) ||
          (listener.workflow_run.branch_taint_reachable &&
           (candidate_source.include?(:branch) || trusted_source.include?(:branch)))
      end
      path = listener.entry.relative_path
      next unless reachable && !cross_exposures.fetch(path).include?(:opaque)

      cross_exposures.fetch(path) << :opaque
      changed = true
    end
  end

  [candidate, trusted, cross_exposures]
end


end
