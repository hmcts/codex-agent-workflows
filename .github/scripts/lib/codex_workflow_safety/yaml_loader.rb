# frozen_string_literal: true

module CodexWorkflowSafety
  extend self

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

end
