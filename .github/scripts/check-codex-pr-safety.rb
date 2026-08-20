#!/usr/bin/env ruby
# frozen_string_literal: true

require_relative "lib/codex_workflow_safety/types"
require_relative "lib/codex_workflow_safety/yaml_loader"
require_relative "lib/codex_workflow_safety/triggers"
require_relative "lib/codex_workflow_safety/event_graph"
require_relative "lib/codex_workflow_safety/credentials"
require_relative "lib/codex_workflow_safety/trusted_review"
require_relative "lib/codex_workflow_safety/runner"

exit CodexWorkflowSafety.run(ARGV)
