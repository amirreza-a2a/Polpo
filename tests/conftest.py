# ============================================================
#  tests/conftest.py
# ============================================================

import sys
import os

# Default to headless Qt execution across all platforms when no display server or
# platform plugin is explicitly configured. setdefault ensures explicit environment
# overrides (e.g. QT_QPA_PLATFORM=minimal) are preserved.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import tests.characterization.conftest_base
