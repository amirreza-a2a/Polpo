# ============================================================
#  infrastructure/pdf/runtime_discovery.py
#  Quarto and XeLaTeX Runtime Discovery & Diagnostics Adapter
#  Phase 13 — Ticket #98
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import IPdfRuntimeDiscovery

_MINIMUM_QUARTO_VERSION = (1, 4, 0)
_WIN32_CREATE_NO_WINDOW = 0x08000000


def _parse_version_tuple(version_str: str) -> Tuple[int, ...]:
    """Parse semver-like version string into integer tuple for comparison."""
    match = re.search(r"(\d+(?:\.\d+)*)", version_str)
    if not match:
        return (0, 0, 0)
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


class QuartoRuntimeDiscovery(IPdfRuntimeDiscovery):
    """
    Infrastructure adapter for probing Quarto CLI and XeLaTeX toolchain.

    Implements dynamic PATH discovery, version capability verification,
    distribution discrimination (TinyTeX vs TeX Live / MiKTeX), and
    cross-platform setup diagnostics without hardcoded machine paths.
    """

    def probe_readiness(
        self,
        configured_quarto_path: Optional[str] = None,
    ) -> EngineReadinessDTO:
        """
        Probes host system for Quarto and XeLaTeX runtimes.
        Never throws unhandled exceptions; returns diagnostic DTO on failures.
        """
        is_windows = platform.system() == "Windows"
        missing_prereqs: List[str] = []
        instructions: List[str] = []

        # 1. Locate Quarto executable
        quarto_path = self._resolve_quarto_executable(configured_quarto_path, is_windows)
        quarto_version_str = ""
        quarto_available = False

        if quarto_path:
            quarto_version_str, quarto_available, version_err = self._probe_quarto_version(
                quarto_path, is_windows
            )
            if not quarto_available:
                if quarto_version_str:
                    missing_prereqs.append("quarto (version >= 1.4.0 required)")
                else:
                    missing_prereqs.append("quarto")
                if version_err:
                    instructions.append(version_err)
        else:
            missing_prereqs.append("quarto")
            instructions.append(self._get_quarto_install_instructions(is_windows))

        # 2. Locate and probe XeLaTeX compiler
        xelatex_path = self._resolve_xelatex_executable(is_windows)
        xelatex_available = False
        is_tinytex = False
        tex_dist_name = ""

        if xelatex_path:
            xelatex_available = True
            is_tinytex, tex_dist_name = self._identify_tex_distribution(xelatex_path)
        else:
            missing_prereqs.append("xelatex")
            instructions.append(self._get_xelatex_install_instructions(is_windows))

        diagnostic_text = "\n\n".join(instructions).strip()

        return EngineReadinessDTO(
            quarto_available=quarto_available,
            quarto_version=quarto_version_str,
            quarto_path=quarto_path,
            xelatex_available=xelatex_available,
            is_tinytex=is_tinytex,
            tex_distribution_name=tex_dist_name,
            missing_prerequisites=tuple(missing_prereqs),
            diagnostic_instructions=diagnostic_text,
        )

    def _resolve_quarto_executable(
        self,
        configured_path: Optional[str],
        is_windows: bool,
    ) -> Optional[str]:
        """Resolves Quarto executable respecting configured path precedence."""
        if configured_path and configured_path.strip():
            stripped = configured_path.strip()
            p = Path(stripped)
            if p.is_file():
                return str(p.resolve())
            if p.is_dir():
                candidates = ["quarto.cmd", "quarto.exe", "quarto"] if is_windows else ["quarto"]
                for cand in candidates:
                    cand_path = p / cand
                    if cand_path.is_file():
                        return str(cand_path.resolve())
            if is_windows:
                for ext in (".cmd", ".exe"):
                    p_ext = p.with_suffix(ext)
                    if p_ext.is_file():
                        return str(p_ext.resolve())
            if p.is_absolute() or "/" in stripped or "\\" in stripped:
                return stripped
            resolved = shutil.which(stripped)
            if resolved:
                return resolved
            return stripped

        # System PATH discovery
        candidates = ["quarto.cmd", "quarto.exe", "quarto"] if is_windows else ["quarto"]
        for cand in candidates:
            resolved = shutil.which(cand)
            if resolved:
                return resolved

        return None

    def _probe_quarto_version(
        self,
        quarto_path: str,
        is_windows: bool,
    ) -> Tuple[str, bool, Optional[str]]:
        """Executes 'quarto --version' and verifies >= 1.4.0 capability floor."""
        try:
            kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 5.0,
            }
            if is_windows:
                kwargs["creationflags"] = _WIN32_CREATE_NO_WINDOW

            proc = subprocess.run([quarto_path, "--version"], **kwargs)
            if proc.returncode != 0:
                return "", False, f"Quarto execution failed with exit code {proc.returncode}."

            version_str = proc.stdout.strip()
            v_tuple = _parse_version_tuple(version_str)
            if v_tuple < _MINIMUM_QUARTO_VERSION:
                err_msg = (
                    f"Quarto version {version_str} is below the minimum required version "
                    f"1.4.0. Please upgrade Quarto to 1.4.0 or newer."
                )
                return version_str, False, err_msg

            return version_str, True, None
        except Exception as e:
            return "", False, f"Failed to execute Quarto at '{quarto_path}': {e}"

    def _resolve_xelatex_executable(self, is_windows: bool) -> Optional[str]:
        """Locates XeLaTeX executable on host system."""
        candidates = ["xelatex.exe", "xelatex"] if is_windows else ["xelatex"]
        for cand in candidates:
            resolved = shutil.which(cand)
            if resolved:
                return resolved

        # Check standard user TinyTeX paths if not on system PATH
        home = Path.home()
        tinytex_search = [
            home / ".TinyTeX" / "bin",
            home / "AppData" / "Roaming" / "TinyTeX" / "bin",
            home / "AppData" / "Local" / "TinyTeX" / "bin",
            home / "AppData" / "Local" / "Programs" / "TinyTeX" / "bin",
        ]
        for base in tinytex_search:
            try:
                if base.exists():
                    for root, _, files in os.walk(base):
                        for file in files:
                            if file.lower() in ("xelatex.exe", "xelatex"):
                                return str(Path(root) / file)
            except Exception:
                pass

        return None

    def _identify_tex_distribution(self, xelatex_path: str) -> Tuple[bool, str]:
        """Determines whether XeLaTeX is managed by TinyTeX, TeX Live, or MiKTeX."""
        path_lower = xelatex_path.lower()
        if "tinytex" in path_lower:
            return True, "TinyTeX"
        if "miktex" in path_lower:
            return False, "MiKTeX"
        return False, "TeX Live"

    def _get_quarto_install_instructions(self, is_windows: bool) -> str:
        """Returns platform-specific installation instructions for Quarto."""
        if is_windows:
            return (
                "Quarto CLI is not installed or not found on PATH.\n"
                "Install Quarto using Windows Package Manager:\n"
                "  winget install --id Posit.Quarto\n"
                "Or download the official installer from:\n"
                "  https://quarto.org/docs/get-started/"
            )
        system = platform.system()
        if system == "Darwin":
            return (
                "Quarto CLI is not installed or not found on PATH.\n"
                "Install Quarto using Homebrew:\n"
                "  brew install --cask quarto\n"
                "Or download the installer from:\n"
                "  https://quarto.org/docs/get-started/"
            )
        return (
            "Quarto CLI is not installed or not found on PATH.\n"
            "Download and install the official Quarto .deb package from:\n"
            "  https://quarto.org/docs/get-started/"
        )

    def _get_xelatex_install_instructions(self, is_windows: bool) -> str:
        """Returns platform-specific installation instructions for XeLaTeX."""
        if is_windows:
            return (
                "XeLaTeX compiler was not found.\n"
                "You can install TinyTeX using Quarto:\n"
                "  quarto install tinytex\n"
                "Or install MiKTeX from:\n"
                "  https://miktex.org/download"
            )
        system = platform.system()
        if system == "Darwin":
            return (
                "XeLaTeX compiler was not found.\n"
                "You can install TinyTeX using Quarto:\n"
                "  quarto install tinytex\n"
                "Or install MacTeX from:\n"
                "  brew install --cask mactex-no-gui"
            )
        return (
            "XeLaTeX compiler was not found.\n"
            "Install TeX Live on Debian/Ubuntu:\n"
            "  sudo apt install texlive-xetex texlive-latex-extra\n"
            "Or install TinyTeX via Quarto:\n"
            "  quarto install tinytex"
        )
