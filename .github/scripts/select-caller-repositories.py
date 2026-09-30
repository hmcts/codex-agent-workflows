#!/usr/bin/env python3
"""Select the caller repositories a release updates, as a job matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


CONFIG = Path(__file__).parents[1] / "config"
REGISTRY = CONFIG / "caller-repositories.json"
RUNNER_TARGETS = CONFIG / "runner-targets.json"
CALLER_SETS = ("juror", "appreg")
FIELDS = {
    "repository",
    "set",
    "branch",
    "runner_group",
    "model_environment",
    "publisher_environment",
}
REPOSITORY = re.compile(r"^hmcts/[A-Za-z0-9._-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
ENVIRONMENT = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*)?$")


class CallerRegistryError(ValueError):
    pass


def load_callers(registry: Path = REGISTRY, runner_targets: Path = RUNNER_TARGETS) -> list[dict[str, str]]:
    groups = {name for name in json.loads(runner_targets.read_text(encoding="utf-8")) if not name.startswith("_")}
    callers = json.loads(registry.read_text(encoding="utf-8")).get("callers")
    if not isinstance(callers, list) or not callers:
        raise CallerRegistryError("the caller registry lists no callers")

    repositories: set[str] = set()
    for caller in callers:
        if not isinstance(caller, dict) or set(caller) != FIELDS:
            raise CallerRegistryError(f"caller entries need exactly {', '.join(sorted(FIELDS))}: {caller!r}")
        if not all(isinstance(value, str) for value in caller.values()):
            raise CallerRegistryError(f"caller fields must be strings: {caller!r}")
        repository = caller["repository"]
        if not REPOSITORY.fullmatch(repository):
            raise CallerRegistryError(f"not an HMCTS repository name: {repository}")
        if repository in repositories:
            raise CallerRegistryError(f"{repository} is listed twice")
        repositories.add(repository)
        if caller["set"] not in CALLER_SETS:
            raise CallerRegistryError(f"{repository} has an unknown caller set: {caller['set']}")
        if not BRANCH.fullmatch(caller["branch"]):
            raise CallerRegistryError(f"{repository} has an invalid branch: {caller['branch']}")
        if caller["runner_group"] not in groups:
            raise CallerRegistryError(f"{repository} names a runner group runner-targets.json lacks: {caller['runner_group']}")
        for field in ("model_environment", "publisher_environment"):
            if not ENVIRONMENT.fullmatch(caller[field]):
                raise CallerRegistryError(f"{repository} has an invalid {field}: {caller[field]}")
        if bool(caller["model_environment"]) != bool(caller["publisher_environment"]):
            raise CallerRegistryError(f"{repository} must name both environments or neither")
    return callers


def select(callers: list[dict[str, str]], caller_set: str) -> list[dict[str, str]]:
    if caller_set not in (*CALLER_SETS, "all"):
        raise CallerRegistryError(f"callers must be {', '.join(CALLER_SETS)} or all, not {caller_set!r}")
    selected = [caller for caller in callers if caller_set in ("all", caller["set"])]
    if not selected:
        raise CallerRegistryError(f"no callers belong to {caller_set}")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--callers", required=True)
    args = parser.parse_args(argv)
    try:
        selected = select(load_callers(), args.callers)
    except CallerRegistryError as error:
        print(f"::error title=Invalid caller selection::{error}", file=sys.stderr)
        return 1
    print("matrix=" + json.dumps({"include": selected}, separators=(",", ":")))
    for caller in selected:
        print(f"Selected {caller['repository']} ({caller['set']}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
