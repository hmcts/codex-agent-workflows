#!/usr/bin/env python3
"""Identify the explicit HTTP 404 emitted by gh for a Contents API request."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


HTTP_404 = re.compile(r"(?<![0-9])HTTP 404(?![0-9])")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("error_file", type=Path)
    args = parser.parse_args()
    return 0 if HTTP_404.search(args.error_file.read_text(encoding="utf-8")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
