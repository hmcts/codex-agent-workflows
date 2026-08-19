#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("codex-recover-pr-state.py")
SPEC = importlib.util.spec_from_file_location("codex_recover_pr_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

REPOSITORY = "hmcts/example"
BASE_REF = "master"
BASE_SHA = "b" * 40
HEAD_REF = "codex/example"
HEAD_SHA = "a" * 40


def pull_request(
    number: int,
    *,
    repository: str = REPOSITORY,
    base_ref: str = BASE_REF,
    base_sha: object = BASE_SHA,
    head_repository: str = REPOSITORY,
    head_ref: str = HEAD_REF,
    head_sha: str = HEAD_SHA,
    draft: bool = False,
) -> dict[str, object]:
    return {
        "number": number,
        "html_url": f"https://github.com/{repository}/pull/{number}",
        "state": "open",
        "draft": draft,
        "base": {
            "ref": base_ref,
            "sha": base_sha,
            "repo": {"full_name": repository},
        },
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"full_name": head_repository},
        },
    }


def recover(
    pull_requests: list[dict[str, object]],
    *,
    draft: bool = False,
    base_sha: str = BASE_SHA,
):
    return MODULE.recover_pull_request(
        pull_requests,
        repository=REPOSITORY,
        base_ref=BASE_REF,
        base_sha=base_sha,
        head_ref=HEAD_REF,
        head_sha=HEAD_SHA,
        draft=draft,
        allow_missing=False,
    )


class PullRequestRecoveryTests(unittest.TestCase):
    def test_accepts_exact_valid_recovery(self):
        state = recover([pull_request(42)])

        self.assertEqual(
            state,
            {
                "found": True,
                "branch_name": HEAD_REF,
                "commit_sha": HEAD_SHA,
                "pr_url": f"https://github.com/{REPOSITORY}/pull/42",
                "pr_number": 42,
            },
        )

    def test_selects_exact_head_instead_of_first_unrelated_pull_request(self):
        state = recover(
            [
                pull_request(1, head_ref="codex/unrelated"),
                pull_request(42),
            ]
        )

        self.assertEqual(state["pr_number"], 42)

    def test_rejects_wrong_base_repository_or_ref(self):
        cases = {
            "repository": pull_request(42, repository="hmcts/other"),
            "base ref": pull_request(42, base_ref="develop"),
        }
        for case, candidate in cases.items():
            with self.subTest(case=case):
                with self.assertRaisesRegex(MODULE.RecoveryError, case):
                    recover([candidate])

    def test_rejects_missing_recovered_base_sha(self):
        with self.assertRaisesRegex(MODULE.RecoveryError, "base SHA"):
            recover([pull_request(42, base_sha=None)])

    def test_rejects_stale_or_advanced_recovered_base_sha(self):
        for case, candidate_sha in (
            ("stale", "c" * 40),
            ("advanced", "d" * 40),
        ):
            with self.subTest(case=case):
                with self.assertRaisesRegex(MODULE.RecoveryError, "base SHA"):
                    recover([pull_request(42, base_sha=candidate_sha)])

    def test_rejects_missing_or_malformed_expected_base_sha(self):
        for base_sha in ("", "abc", "B" * 40, "g" * 40):
            with self.subTest(base_sha=base_sha):
                with self.assertRaisesRegex(
                    MODULE.RecoveryError, "Expected base SHA is missing"
                ):
                    recover([pull_request(42)], base_sha=base_sha)

    def test_rejects_fork_or_wrong_head_repository(self):
        with self.assertRaisesRegex(MODULE.RecoveryError, "head repository"):
            recover([pull_request(42, head_repository="contributor/example")])

    def test_rejects_wrong_head_sha(self):
        with self.assertRaisesRegex(MODULE.RecoveryError, "head SHA"):
            recover([pull_request(42, head_sha="b" * 40)])

    def test_rejects_ready_pull_request_when_draft_is_required_and_inverse(self):
        for expected_draft, actual_draft in ((True, False), (False, True)):
            with self.subTest(
                expected_draft=expected_draft, actual_draft=actual_draft
            ):
                with self.assertRaisesRegex(MODULE.RecoveryError, "draft state"):
                    recover(
                        [pull_request(42, draft=actual_draft)],
                        draft=expected_draft,
                    )

    def test_rejects_multiple_pull_requests_for_expected_head(self):
        with self.assertRaisesRegex(MODULE.RecoveryError, "Multiple open pull requests"):
            recover([pull_request(41), pull_request(42)])

    def test_allow_missing_only_accepts_absence_of_expected_head(self):
        state = MODULE.recover_pull_request(
            [pull_request(1, head_ref="codex/unrelated")],
            repository=REPOSITORY,
            base_ref=BASE_REF,
            base_sha=BASE_SHA,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            draft=False,
            allow_missing=True,
        )

        self.assertEqual(state, {"found": False})


class FreshPullRequestRecoveryTests(unittest.TestCase):
    def recover_fresh(
        self,
        discovered: dict[str, object],
        fresh: dict[str, object],
    ) -> dict[str, object]:
        with mock.patch.object(
            MODULE,
            "fetch_pull_request_by_number",
            return_value=fresh,
        ) as fetch:
            state = MODULE.recover_fresh_pull_request(
                [discovered],
                repository=REPOSITORY,
                base_ref=BASE_REF,
                base_sha=BASE_SHA,
                head_ref=HEAD_REF,
                head_sha=HEAD_SHA,
                draft=False,
                allow_missing=False,
            )
        fetch.assert_called_once_with(REPOSITORY, 42)
        return state

    def test_unchanged_fresh_record_is_the_only_emitted_state(self):
        discovered = pull_request(42)
        discovered["html_url"] = "https://github.com/hmcts/example/pull/stale"
        fresh = pull_request(42)

        state = self.recover_fresh(discovered, fresh)

        self.assertEqual(state["pr_number"], 42)
        self.assertEqual(state["pr_url"], fresh["html_url"])

    def test_rejects_each_field_moving_after_paginated_discovery(self):
        changes = {
            "repository": lambda pull: pull["base"]["repo"].update(
                {"full_name": "hmcts/other"}
            ),
            "base ref": lambda pull: pull["base"].update({"ref": "develop"}),
            "base SHA": lambda pull: pull["base"].update({"sha": "c" * 40}),
            "head ref": lambda pull: pull["head"].update({"ref": "codex/other"}),
            "head repository": lambda pull: pull["head"]["repo"].update(
                {"full_name": "contributor/example"}
            ),
            "head SHA": lambda pull: pull["head"].update({"sha": "d" * 40}),
            "state": lambda pull: pull.update({"state": "closed"}),
            "draft state": lambda pull: pull.update({"draft": True}),
        }
        for field, mutate in changes.items():
            with self.subTest(field=field):
                fresh = copy.deepcopy(pull_request(42))
                mutate(fresh)
                with self.assertRaisesRegex(MODULE.RecoveryError, field):
                    self.recover_fresh(pull_request(42), fresh)

    def test_fails_closed_when_fresh_fetch_fails(self):
        with mock.patch.object(
            MODULE,
            "fetch_pull_request_by_number",
            side_effect=MODULE.RecoveryError("HTTP 502"),
        ):
            with self.assertRaisesRegex(MODULE.RecoveryError, "HTTP 502"):
                MODULE.recover_fresh_pull_request(
                    [pull_request(42)],
                    repository=REPOSITORY,
                    base_ref=BASE_REF,
                    base_sha=BASE_SHA,
                    head_ref=HEAD_REF,
                    head_sha=HEAD_SHA,
                    draft=False,
                    allow_missing=False,
                )


if __name__ == "__main__":
    unittest.main()
