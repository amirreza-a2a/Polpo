# ============================================================
#  tests/conftest.py
# ============================================================

import sys
import os

# Default to headless Qt execution across all platforms when no display server or
# platform plugin is explicitly configured. setdefault ensures explicit environment
# overrides (e.g. QT_QPA_PLATFORM=minimal) are preserved.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

# Default to a deterministic in-memory keyring backend across all platforms so
# unit and integration tests run hermetically without host keyring dependencies
# or Secret Service daemon requirements in headless/containerized CI.
import keyring
from keyring.backend import KeyringBackend


class TestMemoryKeyring(KeyringBackend):
    """Deterministic in-memory keyring backend for isolated test execution."""

    priority = 10

    def __init__(self):
        self._vault = {}

    def get_password(self, service, username):
        return self._vault.get(f"{service}::{username}")

    def set_password(self, service, username, password):
        self._vault[f"{service}::{username}"] = str(password)

    def delete_password(self, service, username):
        key = f"{service}::{username}"
        if key in self._vault:
            del self._vault[key]
        else:
            raise keyring.errors.PasswordDeleteError("Password not found")


keyring.set_keyring(TestMemoryKeyring())


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import tests.characterization.conftest_base
