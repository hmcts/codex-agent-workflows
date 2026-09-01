# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

def run(arguments)
  options = {}
  arguments = arguments.dup
until arguments.empty?
  option = arguments.shift
  value = arguments.shift
  unless %w[--repository-root --trusted-repository-root].include?(option) && value && !options.key?(option)
    warn "usage: #{$PROGRAM_NAME} --repository-root PATH --trusted-repository-root PATH"
    return 2
  end
  options[option] = File.expand_path(value)
end
unless options.keys.sort == %w[--repository-root --trusted-repository-root].sort
  warn "usage: #{$PROGRAM_NAME} --repository-root PATH --trusted-repository-root PATH"
  return 2
end

repository_root = options.fetch("--repository-root")
trusted_repository_root = options.fetch("--trusted-repository-root")

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

trusted_entries = {}
begin
  trusted_entries, discovery_errors = discover_workflows(trusted_repository_root)
  errors.concat(discovery_errors.map { |error| "trusted:#{error}" })
rescue WorkflowSafetyError => error
  errors << "trusted:.github/workflows: #{error.message}"
rescue StandardError => error
  errors << "trusted:.github/workflows: parser failure #{error.class}: #{error.message.lines.first.strip}"
end

candidate_analyses = {}
trusted_analyses = {}
trusted_cross_exposures = {}
if errors.empty?
  begin
    candidate_analyses, trusted_analyses, trusted_cross_exposures =
      analyze_two_tree_exposure(entries, trusted_entries)
  rescue WorkflowSafetyError => error
    errors << error.message
  rescue StandardError => error
    errors << ".github/workflows: graph analysis failure #{error.class}: #{error.message.lines.first.strip}"
  end
end

candidate_analyses.each_value do |analysis|
  next if analysis.exposures.empty?

  begin
    if trusted_review_candidate?(analysis.entry.workflow)
      approved = trusted_analyses[analysis.entry.relative_path]
      unless approved && trusted_review_candidate?(approved.entry.workflow)
        raise WorkflowSafetyError,
              "trusted review dispatch has no approved immutable default-branch wrapper at the same path"
      end
      enforce_trusted_review_dispatch!(analysis, approved)
    else
      enforce_policy!(analysis.entry, entries)
    end
  rescue WorkflowSafetyError => error
    errors << "#{analysis.entry.relative_path}: #{error.message}"
  rescue StandardError => error
    errors << "#{analysis.entry.relative_path}: parser failure #{error.class}: #{error.message.lines.first.strip}"
  end
end

trusted_analyses.each_value do |analysis|
  next if trusted_cross_exposures.fetch(analysis.entry.relative_path, Set.new).empty?

  begin
    if trusted_review_candidate?(analysis.entry.workflow)
      enforce_trusted_review_dispatch!(analysis)
    else
      enforce_policy!(analysis.entry, trusted_entries)
    end
  rescue WorkflowSafetyError => error
    errors << "trusted:#{analysis.entry.relative_path}: #{error.message}"
  rescue StandardError => error
    errors << "trusted:#{analysis.entry.relative_path}: parser failure #{error.class}: #{error.message.lines.first.strip}"
  end
end

unless errors.empty?
  warn "::error title=Unsafe generated-code credential exposure::Autonomous Codex publication is blocked: #{errors.join('; ')}"
  return 1
end

  puts "Caller workflows reachable from untrusted revisions or downstream workflow_run chains are explicitly isolated."
  0
end

end
