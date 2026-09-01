#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("is-explicit-contents-404.py")


class ExplicitContents404Tests(unittest.TestCase):
    def classify(self, message: str) -> int:
        with tempfile.TemporaryDirectory() as temporary_directory:
            error_file = Path(temporary_directory) / "gh-error"
            error_file.write_text(message, encoding="utf-8")
            return subprocess.run(
                ["python3", "-I", str(SCRIPT), str(error_file)], check=False
            ).returncode

    def test_accepts_explicit_http_404(self):
        self.assertEqual(self.classify("gh: Not Found (HTTP 404)\n"), 0)

    def test_rejects_every_other_api_or_transport_failure(self):
        for message in (
            "gh: Resource not accessible by integration (HTTP 403)\n",
            "gh: API rate limit exceeded (HTTP 429)\n",
            "gh: Internal Server Error (HTTP 500)\n",
            "request timed out\n",
        ):
            with self.subTest(message=message):
                self.assertEqual(self.classify(message), 1)


if __name__ == "__main__":
    unittest.main()
