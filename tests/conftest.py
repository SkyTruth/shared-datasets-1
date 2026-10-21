"""Offline notification boundaries apply before test collection and in children."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SAFETY = Path(__file__).parent / "notification_safety"
spec = importlib.util.spec_from_file_location("notification_test_safety", SAFETY / "sitecustomize.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
# Python children load the same audit hook before running their entrypoint.
os.environ["PYTHONPATH"] = str(SAFETY) + os.pathsep + os.environ.get("PYTHONPATH", "")
# Node's preload runs before CommonJS and ESM entrypoints and is inherited.
os.environ["NODE_OPTIONS"] = '--require="' + str(SAFETY / "node_guard.cjs") + '" ' + os.environ.get("NODE_OPTIONS", "")
