#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).parent
JIRA_PUBLISHER = SCRIPT_DIR / "codex-jira-publish.sh"
REVIEW_PUBLISHER = SCRIPT_DIR / "codex-pr-review-publish.sh"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
NEW_SHA = "c" * 40
MOVED_SHA = "d" * 40
LOCAL_TREE_SHA = "e" * 40
OTHER_TREE_SHA = "f" * 40
