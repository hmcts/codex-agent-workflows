#!/usr/bin/env python3
"""Tests for validate-runner-target.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "validate_runner_target",
    Path(__file__).with_name("validate-runner-target.py"),
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ValidateRunnerTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = MODULE.load_targets()

    def test_shipped_config_is_not_empty(self) -> None:
        self.assertTrue(self.targets)
        for group, labels in self.targets.items():
            self.assertTrue(labels, f"group {group} has no labels")

    def test_known_pairs_are_accepted(self) -> None:
        for group, labels in self.targets.items():
            for label in labels:
                self.assertEqual([], MODULE.validate(group, label, self.targets))

    def test_unknown_group_is_refused(self) -> None:
        errors = MODULE.validate("not-a-group", "codex-pilot-azure-aks", self.targets)
        self.assertTrue(any("not allow-listed" in e for e in errors))

    def test_label_from_another_group_is_refused(self) -> None:
        errors = MODULE.validate("juror-codex", "codex-pilot-azure-aks", self.targets)
        self.assertTrue(any("not allow-listed for group" in e for e in errors))

    def test_empty_group_is_refused_with_an_explanation(self) -> None:
        errors = MODULE.validate("", "codex-pilot-azure-aks", self.targets)
        self.assertTrue(any("creates no jobs" in e for e in errors))

    def test_empty_label_is_refused(self) -> None:
        self.assertTrue(MODULE.validate("appreg-codex", "", self.targets))


if __name__ == "__main__":
    unittest.main()
