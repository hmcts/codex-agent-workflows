#!/usr/bin/env python3

from publisher_execution_harness import PublisherExecutionHarnessMixin
from publisher_fake_tools import PublisherFakeToolsMixin
from publisher_git_race import PublisherGitRaceMixin
from publisher_test_constants import *  # noqa: F403


class PublisherTestCase(
    PublisherExecutionHarnessMixin,
    PublisherFakeToolsMixin,
    PublisherGitRaceMixin,
    unittest.TestCase,
):
    pass
