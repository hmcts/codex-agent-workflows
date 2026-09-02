#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any


MODULE_PATH = Path(__file__).with_name("codex-verify-publisher.py")
SPEC = importlib.util.spec_from_file_location("codex_verify_publisher", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Response(io.BytesIO):
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class PublisherValidationTests(unittest.TestCase):
    def payloads(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            {
                "total_count": 2,
                "repositories": [
                    {"full_name": "hmcts/codex-agent-workflows"},
                    {"full_name": "hmcts/appreg-api"},
                ],
            },
            {
                "full_name": "hmcts/appreg-api",
            },
            {"login": "hmcts-codex-agent[bot]", "id": 98765, "type": "Bot"},
        )

    def validate(self, **overrides: Any) -> tuple[str, str]:
        repositories, repository, bot = self.payloads()
        return MODULE.validate_publisher(
            overrides.get("app_slug", "hmcts-codex-agent"),
            overrides.get("installation_id", "12345"),
            overrides.get("repository_name", "hmcts/appreg-api"),
            overrides.get("repositories", repositories),
            overrides.get("repository", repository),
            overrides.get("bot", bot),
        )

    def test_accepts_expected_app_installation(self) -> None:
        login, email = self.validate()
        self.assertEqual(login, "hmcts-codex-agent[bot]")
        self.assertEqual(email, "98765+hmcts-codex-agent[bot]@users.noreply.github.com")

    def test_rejects_invalid_app_slug(self) -> None:
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "valid GitHub App slug"):
            self.validate(app_slug="../another-app")

    def test_rejects_invalid_installation_id(self) -> None:
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "is not valid"):
            self.validate(installation_id="not-an-id")

    def test_rejects_installation_owned_by_another_account(self) -> None:
        repositories, _, _ = self.payloads()
        repositories["repositories"][0]["full_name"] = "another-org/codex-agent-workflows"
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "owned by hmcts"):
            self.validate(repositories=repositories)

    def test_rejects_token_without_expected_repository(self) -> None:
        repositories, _, _ = self.payloads()
        repositories["repositories"] = [
            {"full_name": "hmcts/codex-agent-workflows"}
        ]
        repositories["total_count"] = 1
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "cannot access expected"):
            self.validate(repositories=repositories)

    def test_rejects_invalid_accessible_repositories_payload(self) -> None:
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "invalid accessible"):
            self.validate(repositories={"total_count": 1, "repositories": "invalid"})

    def test_does_not_treat_repository_push_field_as_app_permission(self) -> None:
        _, repository, _ = self.payloads()
        repository["permissions"] = {"pull": True, "push": False}
        login, _ = self.validate(repository=repository)
        self.assertEqual(login, "hmcts-codex-agent[bot]")

    def test_rejects_unexpected_repository(self) -> None:
        _, repository, _ = self.payloads()
        repository["full_name"] = "hmcts/another-repository"
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "unexpected repository"):
            self.validate(repository=repository)

    def test_rejects_invalid_repository_name(self) -> None:
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "valid owner/repository"):
            self.validate(repository_name="../appreg-api")

    def test_rejects_non_bot_identity(self) -> None:
        _, _, bot = self.payloads()
        bot["type"] = "User"
        with self.assertRaisesRegex(MODULE.PublisherVerificationError, "bot identity"):
            self.validate(bot=bot)

    def test_client_uses_bearer_token_without_putting_it_in_url(self) -> None:
        captured: dict[str, Any] = {}

        def opener(request: Any, timeout: int) -> Response:
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return Response(json.dumps({"id": 12345}).encode())

        client = MODULE.GitHubClient("https://api.github.test", "test-secret", opener=opener)
        self.assertEqual(
            client.get_json("/installation/repositories?per_page=100"),
            {"id": 12345},
        )
        self.assertEqual(
            captured["url"],
            "https://api.github.test/installation/repositories?per_page=100",
        )
        self.assertEqual(captured["authorization"], "Bearer test-secret")
        self.assertEqual(captured["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
