"""Discovery and capability verification for Pandoc executable binaries.

Provides prioritized location of bundled, user-configured, or system Pandoc binaries
with active subprocess probe capability validation for commonmark_x+sourcepos and data-pos.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from infrastructure.markdown.exceptions import (
    PandocIncompatibleError,
    PandocNotFoundError,
)
from infrastructure.paths import get_runtime_resource_path

# Required extensions for the CommonMark-extended specification
REQUIRED_COMMONMARK_EXTENSIONS: Sequence[str] = (
    "+tex_math_dollars",
    "+pipe_tables",
    "+task_lists",
)

# Minimum required Pandoc API version for reliable source position attributes
MINIMUM_PANDOC_API_VERSION: Sequence[int] = (1, 23, 0)


def get_current_platform_key() -> str:
    """Detect current operating system and architecture key.

    Returns identifiers matching the acquisition manifest in resources/runtimes.json:
      - linux-x86_64
      - windows-x64
      - darwin-arm64
      - darwin-x86_64
    """
    sys_plat = sys.platform.lower()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine

    if sys_plat.startswith("linux"):
        return f"linux-{arch}"
    if sys_plat in ("win32", "cygwin"):
        return "windows-x64" if arch == "x86_64" else f"windows-{arch}"
    if sys_plat == "darwin":
        return f"darwin-{arch}"

    return f"{sys_plat}-{arch}"


def _has_data_pos(obj: Any) -> bool:
    """Recursively search parsed Pandoc AST JSON for data-pos attribute pairs."""
    if isinstance(obj, dict):
        return any(_has_data_pos(v) for v in obj.values())
    if isinstance(obj, list):
        if len(obj) == 2 and obj[0] == "data-pos" and isinstance(obj[1], str):
            return True
        return any(_has_data_pos(item) for item in obj)
    return False


class PandocBinaryResolver:
    """Discovers and validates Pandoc binary with strict capability verification.

    Search hierarchy:
      1. Bundled resource (resources/bin/{platform}/pandoc)
      2. User-configured override from application settings
      3. System PATH (shutil.which('pandoc'))
    """

    def __init__(
        self,
        configured_path: str | Path | None = None,
        platform_key: str | None = None,
    ) -> None:
        self._configured_path = Path(configured_path) if configured_path else None
        self._platform_key = platform_key

    def get_bundled_path(self) -> Path | None:
        """Find the bundled Pandoc executable for the current platform if present on disk."""
        plat_key = self._platform_key or get_current_platform_key()
        is_windows = plat_key.startswith("win") or sys.platform.startswith("win")
        binary_name = "pandoc.exe" if is_windows else "pandoc"

        # Check manifest target paths and common packaging shorthands
        candidates = [
            f"resources/bin/{plat_key}/{binary_name}",
            f"bin/{plat_key}/{binary_name}",
        ]
        if plat_key in ("linux-x86_64", "linux-amd64"):
            candidates.extend([
                f"resources/bin/linux-x64/{binary_name}",
                f"bin/linux-x64/{binary_name}",
            ])
        elif plat_key == "windows-x64":
            candidates.extend([
                f"resources/bin/win-x64/{binary_name}",
                f"bin/win-x64/{binary_name}",
            ])
        elif plat_key.startswith("darwin"):
            candidates.extend([
                f"resources/bin/macos-universal/{binary_name}",
                f"bin/macos-universal/{binary_name}",
            ])

        # Also check root of bin directory
        candidates.extend([
            f"resources/bin/{binary_name}",
            f"bin/{binary_name}",
        ])

        for rel_candidate in candidates:
            resolved = get_runtime_resource_path(rel_candidate)
            if resolved.is_file():
                return resolved

        return None

    def get_system_path(self) -> Path | None:
        """Find Pandoc on system PATH if available."""
        found = shutil.which("pandoc")
        return Path(found).resolve() if found else None

    def resolve(self, configured_path: str | Path | None = None) -> Path:
        """Resolve a validated Pandoc executable path following the search hierarchy.

        Args:
            configured_path: Optional override path. If omitted, uses path configured at init.

        Returns:
            Resolved absolute Path to a verified Pandoc executable.

        Raises:
            PandocNotFoundError: If no candidate binary exists in bundled, configured, or PATH.
            PandocIncompatibleError: If a candidate binary fails capability or version verification.
        """
        # Priority 1: Bundled runtime binary
        bundled = self.get_bundled_path()
        if bundled is not None and bundled.is_file():
            self.validate_binary(bundled)
            return bundled

        # Priority 2: User-configured override
        raw_configured = configured_path if configured_path is not None else self._configured_path
        active_configured = raw_configured if (raw_configured and str(raw_configured).strip()) else None
        if active_configured is not None:
            conf_path = Path(active_configured).expanduser().resolve()
            if not conf_path.is_file():
                raise PandocNotFoundError(
                    f"Configured Pandoc executable path does not exist: {conf_path}"
                )
            self.validate_binary(conf_path)
            return conf_path

        # Priority 3: System PATH
        system_bin = self.get_system_path()
        if system_bin is not None:
            self.validate_binary(system_bin)
            return system_bin

        # No candidate found across all tiers
        searched_locations = ["bundled resource"]
        if raw_configured and str(raw_configured).strip():
            searched_locations.append(f"configured path '{raw_configured}'")
        searched_locations.append("system PATH")
        raise PandocNotFoundError(
            f"Pandoc executable could not be found. Searched {', '.join(searched_locations)}."
        )

    def validate_binary(self, binary_path: Path | str) -> dict[str, Any]:
        """Validate permissions and verify commonmark_x+sourcepos capability via active probe.

        Args:
            binary_path: Path to the executable binary.

        Returns:
            Parsed probe JSON dictionary.

        Raises:
            PandocIncompatibleError: If binary is invalid, not executable, lacks required extensions,
                                     has an outdated API version, or omits data-pos attributes.
        """
        path = Path(binary_path).expanduser().resolve()

        if not path.is_file():
            raise PandocIncompatibleError(
                f"Target path '{path}' is not a valid file.",
                binary_path=path,
            )

        self._ensure_executable(path)
        self._verify_extensions(path)
        return self._verify_active_probe(path)

    def _ensure_executable(self, path: Path) -> None:
        """Validate and, if needed on POSIX, grant executable permissions."""
        if os.name == "posix":
            if not os.access(path, os.X_OK):
                try:
                    current_mode = path.stat().st_mode
                    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError as err:
                    raise PandocIncompatibleError(
                        f"Binary at '{path}' is not executable and chmod failed: {err}",
                        binary_path=path,
                    ) from err

                if not os.access(path, os.X_OK):
                    raise PandocIncompatibleError(
                        f"Binary at '{path}' remains not executable after applying chmod.",
                        binary_path=path,
                    )

    def _verify_extensions(self, path: Path, timeout: float = 5.0) -> None:
        """Inspect commonmark_x extension support using --list-extensions=commonmark_x."""
        try:
            proc = subprocess.run(
                [str(path), "--list-extensions=commonmark_x"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise PandocIncompatibleError(
                f"Failed to inspect Pandoc extensions for '{path}': {err}",
                binary_path=path,
            ) from err

        if proc.returncode != 0:
            raise PandocIncompatibleError(
                f"Pandoc at '{path}' does not support 'commonmark_x' format "
                f"(exit code {proc.returncode}): {proc.stderr.strip()}",
                binary_path=path,
            )

        lines_set = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        missing = [ext for ext in REQUIRED_COMMONMARK_EXTENSIONS if ext not in lines_set]
        if missing:
            raise PandocIncompatibleError(
                f"Pandoc at '{path}' is missing required extensions for commonmark_x: {', '.join(missing)}",
                binary_path=path,
            )

    def _verify_active_probe(
        self,
        path: Path,
        probe_input: str = "# Probe\n",
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Execute active capability probe verifying commonmark_x+sourcepos and data-pos emission."""
        try:
            proc = subprocess.run(
                [str(path), "-f", "commonmark_x+sourcepos", "-t", "json"],
                input=probe_input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise PandocIncompatibleError(
                f"Failed to execute Pandoc capability probe at '{path}': {err}",
                binary_path=path,
            ) from err

        if proc.returncode != 0:
            raise PandocIncompatibleError(
                f"Pandoc capability probe at '{path}' failed with exit code {proc.returncode}: "
                f"{proc.stderr.strip()}",
                binary_path=path,
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as err:
            raise PandocIncompatibleError(
                f"Pandoc capability probe at '{path}' emitted invalid JSON: {err}",
                binary_path=path,
            ) from err

        if not isinstance(data, dict):
            raise PandocIncompatibleError(
                f"Pandoc capability probe output at '{path}' is not a JSON object: {type(data)}",
                binary_path=path,
            )

        api_version = data.get("pandoc-api-version")
        if not isinstance(api_version, list) or not all(isinstance(x, int) for x in api_version):
            raise PandocIncompatibleError(
                f"Pandoc probe output missing valid 'pandoc-api-version' integer array: {api_version}",
                binary_path=path,
            )

        # Pad version components to at least 3 numbers for consistent tuple comparison
        padded_api_version = tuple(api_version) + (0,) * max(0, 3 - len(api_version))
        if padded_api_version < MINIMUM_PANDOC_API_VERSION:
            version_str = ".".join(map(str, api_version))
            required_str = ".".join(map(str, MINIMUM_PANDOC_API_VERSION))
            raise PandocIncompatibleError(
                f"Pandoc API version '{version_str}' at '{path}' is older than required {required_str}.",
                binary_path=path,
            )

        if not _has_data_pos(data):
            raise PandocIncompatibleError(
                f"Pandoc binary at '{path}' did not emit 'data-pos' attribute tuples "
                f"with format commonmark_x+sourcepos.",
                binary_path=path,
            )

        return data
