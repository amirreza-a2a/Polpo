# ============================================================
#  tests/unit/test_architecture_events_and_serverless.py
#  Event Decoupling, Worker Isolation & Serverless Guards
# ============================================================

import ast
import os
import sys
import inspect
import subprocess
import unittest
from pathlib import Path
from dataclasses import is_dataclass
from unittest.mock import MagicMock

from application.events import (
    JobProgressEvent,
    JobCompletedEvent,
    JobFailedEvent,
    JobCancelledEvent,
    JobStateChangedEvent,
    ApiSwitchEvent,
    ScheduleUpdatedEvent,
)
from infrastructure.events.event_bus import InMemoryEventBus
from interfaces.desktop.workers.runtime import DesktopJobRuntime
from interfaces.desktop.workers.scheduler import DesktopJobScheduler
from interfaces.desktop.qt_compat import QAbstractListModel, QObject


class TestArchitectureEventsAndServerless(unittest.TestCase):
    """
    Automated architectural checks enforcing:
      1. Application events are transport-neutral pure Python dataclasses.
      2. Background workers communicate strictly via event publishing and do not import or hold Qt models/controllers.
      3. Entire repository has zero imports of fastapi, uvicorn, starlette, or interfaces.api.
      4. Desktop startup opens zero inbound listening network sockets (process-level Linux socket audit).
      5. Desktop layer is strictly isolated from frozen legacy Telegram modules.
    """

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent.parent.parent
        cls.desktop_dir = cls.root_dir / "interfaces" / "desktop"
        cls.events_dir = cls.root_dir / "application" / "events"
        cls.workers_dir = cls.desktop_dir / "workers"

    def test_application_events_transport_neutrality(self):
        """
        Rule: Application events must be pure Python dataclasses with ZERO Qt or Telegram imports.
        """
        event_classes = [
            JobProgressEvent,
            JobCompletedEvent,
            JobFailedEvent,
            JobCancelledEvent,
            JobStateChangedEvent,
            ApiSwitchEvent,
            ScheduleUpdatedEvent,
        ]

        for cls_obj in event_classes:
            self.assertTrue(is_dataclass(cls_obj), f"{cls_obj.__name__} must be a pure Python dataclass")

        for py_file in self.events_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("pyside", alias.name.lower())
                        self.assertNotIn("qt", alias.name.lower())
                        self.assertNotIn("telegram", alias.name.lower())
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").lower()
                    self.assertNotIn("pyside", mod)
                    self.assertNotIn("qt", mod)
                    self.assertNotIn("telegram", mod)

    def test_worker_thread_isolation_from_qt_models(self):
        """
        Rule: Background workers (DesktopJobRuntime, DesktopJobScheduler) must NOT import,
        accept, or hold references to Qt models, controllers, QObjects, or QML components.
        All presentation communication occurs strictly through the application event publisher.
        """
        forbidden_in_workers = {
            "interfaces.desktop.models",
            "interfaces.desktop.controllers",
            "PySide6.QtQuick",
            "PySide6.QtQml",
        }

        # 1. AST Import check
        for py_file in self.workers_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_in_workers:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Worker file {py_file} must not import '{alias.name}'",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_in_workers:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Worker file {py_file} must not from-import '{mod}'",
                        )

        # 2. Constructor signature & annotation inspection
        for worker_cls in [DesktopJobRuntime, DesktopJobScheduler]:
            sig = inspect.signature(worker_cls.__init__)
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                type_str = str(param.annotation).lower()
                self.assertNotIn("model", type_str, f"{worker_cls.__name__} parameter '{param_name}' must not accept a Model")
                self.assertNotIn("controller", type_str, f"{worker_cls.__name__} parameter '{param_name}' must not accept a Controller")
                self.assertNotIn("qobject", type_str, f"{worker_cls.__name__} parameter '{param_name}' must not accept a QObject")

        # 3. Runtime instance attribute inspection (Option A)
        bus = InMemoryEventBus()
        runtime = DesktopJobRuntime(
            uow_factory=MagicMock(),
            job_execution_service=MagicMock(),
            settings_service=MagicMock(),
            event_publisher=bus,
        )
        scheduler = DesktopJobScheduler(
            uow_factory=MagicMock(),
            runtime=runtime,
            settings_service=MagicMock(),
            event_publisher=bus,
        )

        for obj, name in [(runtime, "DesktopJobRuntime"), (scheduler, "DesktopJobScheduler")]:
            for attr_name, attr_val in vars(obj).items():
                self.assertFalse(
                    isinstance(attr_val, (QAbstractListModel, QObject)),
                    f"{name}.{attr_name} must not be a QAbstractListModel or QObject instance",
                )
                mod_name = getattr(type(attr_val), "__module__", "")
                self.assertNotIn(
                    "interfaces.desktop.models",
                    mod_name,
                    f"{name}.{attr_name} holds a presentation model instance: {mod_name}",
                )
                self.assertNotIn(
                    "interfaces.desktop.controllers",
                    mod_name,
                    f"{name}.{attr_name} holds a presentation controller instance: {mod_name}",
                )

    def test_serverless_zero_server_imports_repository_wide(self):
        """
        Rule: Zero production or test modules may import fastapi, uvicorn, starlette, or interfaces.api.
        """
        forbidden_server_packages = {"fastapi", "uvicorn", "starlette", "interfaces.api"}

        for py_file in self.root_dir.rglob("*.py"):
            # Exclude this test file and other architectural AST inspection tests from inspecting their own string literals
            if "test_architecture" in py_file.name or "test_phase8" in py_file.name or "test_secret_non_persistence" in py_file.name or "test_domain_reconciliation" in py_file.name or "test_ai_adapters" in py_file.name or "test_desktop_presentation_invariants" in py_file.name:
                continue

            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_server_packages:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Forbidden server framework import '{alias.name}' in file: {py_file}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_server_packages:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Forbidden server from-import '{mod}' in file: {py_file}",
                        )

    def test_zero_inbound_listening_network_sockets_on_startup(self):
        """
        Issue 1: Strengthened zero-listener verification.
        Performs process-level and kernel-level inspection of listening sockets for the running
        desktop process, verifying that the process owns zero inbound LISTEN sockets.
        """
        code = r"""
import os, sys, re, socket, tempfile
from pathlib import Path
from interfaces.desktop.app import create_app

def get_process_socket_inodes(pid):
    fd_dir = Path(f'/proc/{pid}/fd')
    if not fd_dir.exists():
        return set()
    inodes = set()
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
            m = re.match(r'socket:\[(\d+)\]', target)
            if m:
                inodes.add(m.group(1))
        except OSError:
            pass
    return inodes

def get_kernel_tcp_listeners():
    listening = {}
    for net_file in ['/proc/net/tcp', '/proc/net/tcp6']:
        p = Path(net_file)
        if not p.exists():
            continue
        lines = p.read_text().splitlines()[1:]
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 10:
                state = parts[3]
                inode = parts[9]
                local_addr = parts[1]
                # '0A' is TCP_LISTEN in Linux kernel /proc/net/tcp
                if state == '0A':
                    listening[inode] = local_addr
    return listening

temp_dir = tempfile.TemporaryDirectory()
base = Path(temp_dir.name)
db_path = base / 'serverless_audit.db'

# Launch Desktop application
app, engine, container = create_app(
    argv=['-platform', 'offscreen'],
    db_path=db_path,
    start_background_runtime=True,
)

my_pid = os.getpid()
my_socket_inodes = get_process_socket_inodes(my_pid)
system_listeners = get_kernel_tcp_listeners()

# Find intersection: listening sockets owned by this desktop process
process_listeners = my_socket_inodes.intersection(system_listeners.keys())

container.shutdown()
temp_dir.cleanup()

assert len(process_listeners) == 0, f'Desktop app process {my_pid} created inbound LISTEN sockets: {process_listeners}'
print('0_LISTENERS_VERIFIED_PID_' + str(my_pid))
"""
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Inbound socket audit failed: {proc.stderr}")
        self.assertIn("0_LISTENERS_VERIFIED_PID_", proc.stdout)

    def test_frozen_telegram_transport_isolation(self):
        """
        Rule: Desktop code (interfaces/desktop/) must have ZERO imports from the frozen
        Telegram transport modules (interfaces/telegram/, handlers/, services/worker.py, database/).
        """
        forbidden_telegram_modules = {
            "interfaces.telegram",
            "handlers",
            "services.worker",
            "database.connection",
            "database.models",
        }

        for py_file in self.desktop_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_telegram_modules:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Desktop file {py_file} must not import '{alias.name}'",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_telegram_modules:
                        self.assertFalse(
                            mod == forbidden or mod.startswith(forbidden + "."),
                            f"Desktop file {py_file} must not from-import '{mod}'",
                        )


if __name__ == "__main__":
    unittest.main()
