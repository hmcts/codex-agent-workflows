#!/usr/bin/env python3
"""Refuse a caller-supplied runner group and label pair that is not allow-listed.

The runner group's own workflow access list is the enforced boundary. This check
is the earlier, clearer failure: it runs on GitHub-hosted compute before any
credential or model job is scheduled, so a caller that asks for the wrong pair
gets a named error instead of a job that queues forever or lands on unintended
compute.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config" / "runner-targets.json"


def load_targets(path: Path = CONFIG) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def validate(group: str, label: str, targets: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    if not group:
        errors.append("runner_group is empty; an empty group creates no jobs at all")
    if not label:
        errors.append("runner_label is empty")
    if errors:
        return errors
    if group not in targets:
        errors.append(
            f"runner_group {group!r} is not allow-listed. Known groups: "
            + ", ".join(sorted(targets))
        )
        return errors
    if label not in targets[group]:
        errors.append(
            f"runner_label {label!r} is not allow-listed for group {group!r}. "
            "Allowed: " + ", ".join(sorted(targets[group]))
        )
    return errors


def main() -> int:
    group = os.environ.get("RUNNER_GROUP", "").strip()
    label = os.environ.get("RUNNER_LABEL", "").strip()
    errors = validate(group, label, load_targets())
    if errors:
        for message in errors:
            print(f"::error title=Runner target refused::{message}", file=sys.stderr)
        return 1
    print(f"Runner target accepted: group={group} label={label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
