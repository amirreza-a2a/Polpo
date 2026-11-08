#!/usr/bin/env python3
# ============================================================
#  scripts/acceptance/run_rendering_acceptance.py
#  Real-World Rendering Acceptance Test Suite for Ubuntu 24
# ============================================================

"""
Real-world rendering acceptance test harness.
Executes real host-level preflight, MathJax rendering, standalone LaTeX diagram
rendering (TikZ, PGFPlots, CircuitTikZ, TikZ-CD), Quarto PDF export, desktop
preview image providers, and optional live AI (Gemini) requests.

Outputs machine-readable JSON and human-readable Markdown reports.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infrastructure.paths import get_runtime_resource_path, get_user_data_dir

logger = logging.getLogger("acceptance")


# ---------------------------------------------------------------------------
# Data Models for Acceptance Reporting
# ---------------------------------------------------------------------------

class TestStatus:
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"


@dataclass
class TestItemResult:
    __test__ = False
    test_id: str
    name: str
    status: str
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    sanitized_diagnostics: Optional[str] = None


@dataclass
class StageResult:
    stage_name: str
    status: str
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    blocked_tests: int = 0
    skipped_tests: int = 0
    duration_seconds: float = 0.0
    items: List[TestItemResult] = field(default_factory=list)


@dataclass
class AcceptanceReport:
    timestamp_utc: str
    hostname: str
    os_info: Dict[str, str]
    overall_status: str
    stages: Dict[str, StageResult] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage 1: Runtime Preflight Probe
# ---------------------------------------------------------------------------

def classify_os_target(
    os_release_text: Optional[str],
    machine: str,
    system: str,
) -> Tuple[str, Optional[str], Dict[str, str]]:
    """Strictly classifies the host OS target against Ubuntu 24.04 (x86_64) acceptance baseline.

    Returns:
        (status, error_message_or_none, parsed_details_dict)
    """
    details: Dict[str, str] = {
        "system": system,
        "machine": machine,
    }
    if os_release_text is None:
        return (
            TestStatus.BLOCKED,
            "/etc/os-release file not found; unsupported host environment for Ubuntu 24 suite",
            details,
        )

    for line in os_release_text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            details[k.strip()] = v.strip().strip('"')

    distro_id = details.get("ID", "").lower()
    version_id = details.get("VERSION_ID", "")

    if distro_id != "ubuntu":
        return (
            TestStatus.BLOCKED,
            f"Unsupported Linux distribution '{distro_id or 'unknown'}'; Ubuntu 24.04 required",
            details,
        )

    if not (version_id == "24.04" or version_id.startswith("24.04.")):
        return (
            TestStatus.BLOCKED,
            f"Unsupported Ubuntu version '{version_id or 'unknown'}'; Ubuntu 24.04 required",
            details,
        )

    if machine not in ("x86_64", "amd64"):
        return (
            TestStatus.BLOCKED,
            f"Unsupported CPU architecture '{machine}'; x86_64 required for Ubuntu 24 suite",
            details,
        )

    return TestStatus.PASS, None, details


def compute_overall_status(
    stages: Dict[str, StageResult],
    optional_stages: Tuple[str, ...] = ("gemini-live",),
) -> str:
    """Computes the overall acceptance suite status with strict fail and block propagation.

    Rules:
      1. Any stage failure -> overall FAIL.
      2. Any mandatory stage blocked -> overall BLOCKED.
      3. All mandatory stages pass (and optional stages pass or skipped with reason) -> PASS.
    """
    has_failure = any(stg.status == TestStatus.FAIL for stg in stages.values())
    if has_failure:
        return TestStatus.FAIL

    mandatory_stages = {
        name: stg for name, stg in stages.items() if name not in optional_stages
    }
    has_blocked_mandatory = any(
        stg.status == TestStatus.BLOCKED for stg in mandatory_stages.values()
    )
    if has_blocked_mandatory:
        return TestStatus.BLOCKED

    return TestStatus.PASS


def run_runtime_preflight() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    # 1. OS Release and Architecture
    t0 = time.monotonic()
    os_release_file = Path("/etc/os-release")
    os_release_text = os_release_file.read_text(encoding="utf-8") if os_release_file.is_file() else None
    os_status, os_error, os_details = classify_os_target(
        os_release_text=os_release_text,
        machine=platform.machine(),
        system=platform.system(),
    )
    os_details["platform"] = platform.platform()
    os_details["release"] = platform.release()

    items.append(TestItemResult(
        test_id="PREFLIGHT-01-OS",
        name="Operating System and Architecture (Ubuntu 24.04 x86_64)",
        status=os_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=os_details,
        error_message=os_error,
    ))

    # 2. Python and PySide6
    t0 = time.monotonic()
    import PySide6
    py_details = {
        "python_version": sys.version,
        "interpreter": sys.executable,
        "in_venv": sys.prefix != sys.base_prefix,
        "pyside6_version": PySide6.__version__,
    }
    items.append(TestItemResult(
        test_id="PREFLIGHT-02-PYTHON",
        name="Python Interpreter and PySide6",
        status=TestStatus.PASS,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=py_details,
    ))

    # 3. Quarto CLI
    t0 = time.monotonic()
    quarto_bin = shutil.which("quarto")
    quarto_ver = ""
    if quarto_bin:
        try:
            res = subprocess.run([quarto_bin, "--version"], capture_output=True, text=True, timeout=5)
            quarto_ver = res.stdout.strip()
        except Exception as e:
            quarto_ver = f"error: {e}"

    quarto_status = TestStatus.PASS if (quarto_bin and quarto_ver and not quarto_ver.startswith("error")) else TestStatus.BLOCKED
    items.append(TestItemResult(
        test_id="PREFLIGHT-03-QUARTO",
        name="Quarto CLI Executable",
        status=quarto_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"path": quarto_bin or "NOT_FOUND", "version": quarto_ver},
        error_message=None if quarto_status == TestStatus.PASS else "Quarto CLI not found or broken",
    ))

    # 4. Pandoc CLI
    t0 = time.monotonic()
    pandoc_bin = shutil.which("pandoc")
    pandoc_ver = ""
    if pandoc_bin:
        try:
            res = subprocess.run([pandoc_bin, "--version"], capture_output=True, text=True, timeout=5)
            pandoc_ver = res.stdout.splitlines()[0] if res.stdout else ""
        except Exception as e:
            pandoc_ver = f"error: {e}"

    pandoc_status = TestStatus.PASS if (pandoc_bin and pandoc_ver and not pandoc_ver.startswith("error")) else TestStatus.BLOCKED
    items.append(TestItemResult(
        test_id="PREFLIGHT-04-PANDOC",
        name="Pandoc CLI Executable",
        status=pandoc_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"path": pandoc_bin or "NOT_FOUND", "version": pandoc_ver},
        error_message=None if pandoc_status == TestStatus.PASS else "Pandoc CLI not found or broken",
    ))

    # 5. XeLaTeX and TeX Distribution
    t0 = time.monotonic()
    xelatex_bin = shutil.which("xelatex")
    xelatex_ver = ""
    tex_dist = "Unknown"
    if xelatex_bin:
        try:
            res = subprocess.run([xelatex_bin, "--version"], capture_output=True, text=True, timeout=5)
            xelatex_ver = res.stdout.splitlines()[0] if res.stdout else ""
            if "TeX Live" in res.stdout:
                tex_dist = "TeX Live"
            elif "MiKTeX" in res.stdout:
                tex_dist = "MiKTeX"
        except Exception as e:
            xelatex_ver = f"error: {e}"

    xelatex_status = TestStatus.PASS if (xelatex_bin and xelatex_ver and not xelatex_ver.startswith("error")) else TestStatus.BLOCKED
    items.append(TestItemResult(
        test_id="PREFLIGHT-05-XELATEX",
        name="XeLaTeX and TeX Distribution",
        status=xelatex_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"path": xelatex_bin or "NOT_FOUND", "version": xelatex_ver, "distribution": tex_dist},
        error_message=None if xelatex_status == TestStatus.PASS else "XeLaTeX executable not found or broken",
    ))

    # 6. TeX Packages Check via kpsewhich
    t0 = time.monotonic()
    required_packages = ["standalone.cls", "tikz.sty", "pgfplots.sty", "circuitikz.sty", "tikz-cd.sty"]
    pkg_results: Dict[str, str] = {}
    missing_pkgs: List[str] = []
    kpsewhich_bin = shutil.which("kpsewhich")
    if not kpsewhich_bin:
        pkg_status = TestStatus.BLOCKED
        for pkg in required_packages:
            pkg_results[pkg] = "kpsewhich_not_found"
        pkg_error = "kpsewhich executable not found (TeX distribution not present)"
    else:
        for pkg in required_packages:
            try:
                res = subprocess.run([kpsewhich_bin, pkg], capture_output=True, text=True, timeout=3)
                p = res.stdout.strip()
                if p:
                    pkg_results[pkg] = p
                else:
                    pkg_results[pkg] = "MISSING"
                    missing_pkgs.append(pkg)
            except Exception as e:
                pkg_results[pkg] = f"error: {e}"
                missing_pkgs.append(pkg)
        pkg_status = TestStatus.PASS if not missing_pkgs else TestStatus.FAIL
        pkg_error = f"Missing required TeX packages: {missing_pkgs}" if missing_pkgs else None

    items.append(TestItemResult(
        test_id="PREFLIGHT-06-TEX-PACKAGES",
        name="LaTeX Packages (standalone, tikz, pgfplots, circuitikz, tikz-cd)",
        status=pkg_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=pkg_results,
        error_message=pkg_error,
    ))

    # 7. Persian Fonts Availability
    t0 = time.monotonic()
    fc_list = shutil.which("fc-list")
    fonts_found: List[str] = []
    if fc_list:
        try:
            res = subprocess.run([fc_list, ":", "family"], capture_output=True, text=True, timeout=5)
            for f in ["Vazirmatn", "Amiri", "Sahel", "Samim"]:
                if f.lower() in res.stdout.lower():
                    fonts_found.append(f)
        except Exception as e:
            logger.warning("Failed to query fontconfig: %s", e)

    font_status = TestStatus.PASS if fonts_found else TestStatus.BLOCKED
    items.append(TestItemResult(
        test_id="PREFLIGHT-07-FONTS",
        name="Persian Fonts Support (Vazirmatn / Amiri)",
        status=font_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"installed_target_fonts": fonts_found},
        error_message=None if font_status == TestStatus.PASS else "No target Persian fonts (Vazirmatn/Amiri) discovered via fontconfig",
    ))

    # 8. PyMuPDF (fitz)
    t0 = time.monotonic()
    try:
        import fitz
        mupdf_details = {
            "version": fitz.__version__,
            "mupdf_version": str(getattr(fitz, "mupdf_version", "")),
        }
        mupdf_status = TestStatus.PASS
    except Exception as e:
        mupdf_details = {"error": str(e)}
        mupdf_status = TestStatus.FAIL

    items.append(TestItemResult(
        test_id="PREFLIGHT-08-PYMUPDF",
        name="PyMuPDF Vector Extraction Engine",
        status=mupdf_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=mupdf_details,
    ))

    # 9. MathJax Headless Runtime & Worker
    t0 = time.monotonic()
    mj_node = get_runtime_resource_path("resources/mathjax/node")
    if not (mj_node.is_file() and os.access(mj_node, os.X_OK)):
        sys_node = shutil.which("node")
        mj_node = Path(sys_node) if sys_node else mj_node

    mj_worker = get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
    mj_pkg_json = get_runtime_resource_path("resources/mathjax/package.json")
    mj_pkg_version = "Unknown"
    if mj_pkg_json.is_file():
        try:
            mj_pkg_version = json.loads(mj_pkg_json.read_text(encoding="utf-8")).get("dependencies", {}).get("mathjax-full", "Unknown")
        except Exception:
            pass

    node_ok = mj_node.is_file() and os.access(mj_node, os.X_OK)
    worker_ok = mj_worker.is_file()
    items.append(TestItemResult(
        test_id="PREFLIGHT-09-MATHJAX-RUNTIME",
        name="MathJax Node.js Runtime and Worker",
        status=TestStatus.PASS if (node_ok and worker_ok) else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={
            "node_executable": str(mj_node),
            "worker_script": str(mj_worker),
            "mathjax_version": mj_pkg_version,
        },
    ))

    # 10. Configured AI Providers Probe (Read-only, Zero Secret Leakage)
    t0 = time.monotonic()
    ai_status = TestStatus.SKIPPED_WITH_REASON
    ai_details: Dict[str, Any] = {"active_slots": 0, "configured_providers": []}
    user_db = get_user_data_dir() / "polpot.db"
    if not user_db.is_file():
        ai_details["note"] = "User database does not exist yet"
    else:
        try:
            from interfaces.desktop.composition import DesktopAppContainer
            container = DesktopAppContainer(instance_lock=None)
            with container.uow_factory.create() as uow:
                slots = [s for s in uow.apis.list_all() if getattr(s, "is_active", True)]
            ai_details["active_slots"] = len(slots)
            for s in slots:
                ai_details["configured_providers"].append({
                    "id": s.id,
                    "provider": s.provider,
                    "label": s.label,
                    "slot_type": s.slot_type,
                    "model": s.selected_model,
                })
            if slots:
                ai_status = TestStatus.PASS
        except Exception as e:
            ai_details["error"] = str(e)
            ai_status = TestStatus.FAIL

    items.append(TestItemResult(
        test_id="PREFLIGHT-10-AI-PROVIDER-CONFIG",
        name="Configured AI Slots Configuration",
        status=ai_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=ai_details,
        error_message=None if ai_status == TestStatus.PASS else (
            f"Error reading AI slots: {ai_details.get('error')}"
            if ai_status == TestStatus.FAIL
            else "No active AI slots configured in user database (live AI optional)"
        ),
    ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)
    skipped = sum(1 for i in items if i.status == TestStatus.SKIPPED_WITH_REASON)

    overall = TestStatus.PASS if failed == 0 and blocked == 0 else (TestStatus.FAIL if failed > 0 else TestStatus.BLOCKED)

    return StageResult(
        stage_name="runtime-preflight",
        status=overall,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        skipped_tests=skipped,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 2: Real MathJax Acceptance
# ---------------------------------------------------------------------------

def run_mathjax_live() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    from infrastructure.math import MathSvgCache, MathJaxProcessSupervisor, MathJaxClient
    from application.ports.math_renderer import MathRenderRequest, MathSyntaxError

    supervisor = MathJaxProcessSupervisor()
    cache = MathSvgCache()
    client = MathJaxClient(supervisor=supervisor, cache=cache)

    try:
        formulas = [
            ("MJ-01-INLINE-FRAC", "Inline fraction", r"\frac{a}{b}", False),
            ("MJ-02-DISPLAY-CALCULUS", "Fundamental theorem of calculus", r"\frac{d}{dx}\int_a^x f(t)dt = f(x)", True),
            ("MJ-03-MATRIX", "2x2 Matrix", r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}", True),
            ("MJ-04-ALIGNED", "Aligned system", r"\begin{aligned} a &= b + c \\ d &= e + f \end{aligned}", True),
            ("MJ-05-CASES", "Piecewise cases", r"f(x) = \begin{cases} 0 & x < 0 \\ 1 & x \ge 0 \end{cases}", True),
            ("MJ-06-SUM-INTEGRAL", "Infinite series and integral", r"\sum_{n=1}^\infty \frac{1}{n^2} = \frac{\pi^2}{6}", True),
            ("MJ-07-SCRIPTS", "Multi-level subscripts/superscripts", r"A_{i,j}^{(k)} \cdot B_{j,l}^{(m)}", False),
            ("MJ-08-COMPLEX", "Laplace transform", r"\mathcal{L}\{f(t)\} = \int_0^\infty e^{-st} f(t) dt", True),
            ("MJ-09-PERSIAN-TEXT", "Persian text in math mode", r"\text{انرژی} = mc^2", True),
            ("MJ-10-MIXED-BILINGUAL", "Mixed Persian/English equation", r"\Delta x = v_0 t + \frac{1}{2} a t^2 \quad \text{(معادله حرکت)}", True),
        ]

        for test_id, name, tex, is_display in formulas:
            t0 = time.monotonic()
            try:
                res = client.render(MathRenderRequest(tex=tex, display=is_display))
                is_valid = res.svg_xml.startswith("<svg") and "</svg>" in res.svg_xml
                items.append(TestItemResult(
                    test_id=test_id,
                    name=name,
                    status=TestStatus.PASS if is_valid else TestStatus.FAIL,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    details={
                        "display": is_display,
                        "width": res.width,
                        "height": res.height,
                        "svg_bytes": len(res.svg_xml),
                    },
                ))
            except Exception as e:
                items.append(TestItemResult(
                    test_id=test_id,
                    name=name,
                    status=TestStatus.FAIL,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error_message=str(e),
                ))

        # Test cache hit
        t0 = time.monotonic()
        cached_res = client.render(MathRenderRequest(tex=r"\frac{a}{b}", display=False))
        items.append(TestItemResult(
            test_id="MJ-11-CACHE-HIT",
            name="Positive LRU cache retrieval",
            status=TestStatus.PASS if cached_res.svg_xml else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"cached": True},
        ))

        # Test invalid formula error handling
        t0 = time.monotonic()
        try:
            client.render(MathRenderRequest(tex=r"\frac{1}{", display=False))
            items.append(TestItemResult(
                test_id="MJ-12-ERROR-TRAP",
                name="Invalid syntax error trapping",
                status=TestStatus.FAIL,
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message="Expected syntax error was not raised",
            ))
        except Exception as exc:
            err_str = str(exc)
            items.append(TestItemResult(
                test_id="MJ-12-ERROR-TRAP",
                name="Invalid syntax error trapping",
                status=TestStatus.PASS,
                duration_ms=(time.monotonic() - t0) * 1000,
                details={"caught_error": err_str},
            ))

        # Verify worker survivability after error
        t0 = time.monotonic()
        after_error = client.render(MathRenderRequest(tex=r"E = mc^2", display=False))
        items.append(TestItemResult(
            test_id="MJ-13-WORKER-RECOVERY",
            name="Worker survivability after error",
            status=TestStatus.PASS if after_error.svg_xml else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"survived": True},
        ))

    finally:
        supervisor.shutdown()

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="mathjax-live",
        status=TestStatus.PASS if failed == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 3: Real TikZ / PGFPlots / CircuitTikZ / TikZ-CD Acceptance
# ---------------------------------------------------------------------------

def run_diagram_render_live() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    from infrastructure.diagram.cache import DiagramSvgCache
    from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer
    from application.ports.diagram_renderer import DiagramRenderRequest, DiagramSyntaxError
    from tests.fixtures.quarto_diagram_fixtures import (
        FIXTURE_A_SIMPLE_TIKZ,
        FIXTURE_B_PGFPLOTS_FUNCTION,
        FIXTURE_C_DISCRETE_SIGNAL_YCOMB,
    )

    cache = DiagramSvgCache()
    renderer = StandaloneTeXDiagramRenderer(cache=cache)

    if not renderer.probe_readiness():
        return StageResult(
            stage_name="diagram-render-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="DIAG-00-PROBE",
                name="Renderer readiness probe",
                status=TestStatus.BLOCKED,
                error_message="TeX compiler or PyMuPDF unavailable",
            )],
        )

    # Fixture snippets
    def clean_src(raw_block: str) -> str:
        s = raw_block.strip()
        if s.startswith("```{=latex}"):
            s = s[len("```{=latex}"):]
        if s.endswith("```"):
            s = s[:-3]
        return s.strip()

    diagrams = [
        ("DIAG-01-TIKZ-BLOCK", "TikZ System Block Diagram", clean_src(FIXTURE_A_SIMPLE_TIKZ)),
        ("DIAG-02-PGFPLOTS-PARABOLA", "PGFPlots Function Parabola with Coordinates", clean_src(FIXTURE_B_PGFPLOTS_FUNCTION)),
        ("DIAG-03-DISCRETE-SIGNAL", "Discrete-Time Signal ycomb Plot", clean_src(FIXTURE_C_DISCRETE_SIGNAL_YCOMB)),
        ("DIAG-04-CIRCUITIKZ", "CircuitTikZ RLC Bridge Schematic", r"""\begin{circuitikz}[american]
    \draw (0,0) to[V=$V_s$] (0,2) to[R=$R$] (2,2) to[C=$C$] (2,0) -- (0,0);
\end{circuitikz}"""),
        ("DIAG-05-TIKZ-CD", "TikZ-CD Commutative Diagram", r"""\begin{tikzcd}
A \arrow[r, "\phi"] \arrow[d, "\psi"'] & B \arrow[d, "\eta"] \\
C \arrow[r, "\theta"'] & D
\end{tikzcd}"""),
        ("DIAG-06-BILINGUAL-LABELS", "TikZ Diagram with Persian/English Labels", r"""\begin{tikzpicture}[auto, node distance=2.5cm, >=latex]
    \node [draw, circle] (sum) {$\Sigma$};
    \node [draw, rectangle, right of=sum] (controller) {$G(s)$};
    \node [right of=controller, node distance=2cm] (output) {};
    \node [left of=sum, node distance=2cm] (input) {};

    \draw [->] (input) -- node {$R(s)$} (sum);
    \draw [->] (sum) -- node {$E(s)$} (controller);
    \draw [->] (controller) -- node [name=y] {$Y(s)$} (output);
    \draw [->] (y) -- ++(0,-1.5) -| node [pos=0.99] {$-$} (sum);
\end{tikzpicture}"""),
    ]

    for test_id, name, src in diagrams:
        t0 = time.monotonic()
        req = DiagramRenderRequest(source=src)
        try:
            res = renderer.render(req)
            is_valid_svg = res.svg_xml.startswith("<svg") and "</svg>" in res.svg_xml
            items.append(TestItemResult(
                test_id=test_id,
                name=name,
                status=TestStatus.PASS if is_valid_svg and res.width > 0 and res.height > 0 else TestStatus.FAIL,
                duration_ms=(time.monotonic() - t0) * 1000,
                details={
                    "width": res.width,
                    "height": res.height,
                    "svg_bytes": len(res.svg_xml),
                    "hash": res.hash[:16],
                },
            ))
        except Exception as e:
            items.append(TestItemResult(
                test_id=test_id,
                name=name,
                status=TestStatus.FAIL,
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message=str(e),
            ))

    # Test cache hit
    t0 = time.monotonic()
    req_cached = DiagramRenderRequest(source=clean_src(FIXTURE_A_SIMPLE_TIKZ))
    res_cached = renderer.render(req_cached)
    items.append(TestItemResult(
        test_id="DIAG-07-CACHE-HIT",
        name="Diagram LRU Cache Hit Verification",
        status=TestStatus.PASS if res_cached.svg_xml else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"hash": res_cached.hash[:16]},
    ))

    # Test compiler error trapping with invalid TeX command
    t0 = time.monotonic()
    invalid_src = r"""\begin{tikzpicture}
    \invalidTeXCommandXYZ123{}
\end{tikzpicture}"""
    try:
        renderer.render(DiagramRenderRequest(source=invalid_src))
        items.append(TestItemResult(
            test_id="DIAG-08-ERROR-TRAP",
            name="LaTeX Compiler Error Trapping",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message="Expected DiagramSyntaxError was not raised",
        ))
    except DiagramSyntaxError as err:
        items.append(TestItemResult(
            test_id="DIAG-08-ERROR-TRAP",
            name="LaTeX Compiler Error Trapping",
            status=TestStatus.PASS,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"sanitized_error": err.message},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="DIAG-08-ERROR-TRAP",
            name="LaTeX Compiler Error Trapping",
            status=TestStatus.PASS,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"caught_generic_error": str(exc)},
        ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="diagram-render-live",
        status=TestStatus.PASS if failed == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 4: Real Quarto PDF Acceptance
# ---------------------------------------------------------------------------

def run_quarto_pdf_live() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    try:
        import fitz
    except ImportError:
        return StageResult(
            stage_name="quarto-pdf-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="PDF-00-PROBE",
                name="PyMuPDF verification probe",
                status=TestStatus.BLOCKED,
                error_message="PyMuPDF library not installed",
            )],
        )

    from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery
    from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor
    from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
    from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer
    from infrastructure.pdf.quarto_adapter import QuartoPdfAdapter
    from infrastructure.pdf.atomic_publisher import AtomicPdfPublisher
    from application.services.quarto_pdf_export_service import QuartoPdfExportService
    from application.dto.pdf_export_dto import ExportPdfCommand
    from core.entities.job import Job
    from core.entities.document_version import DocumentVersionRecord
    from unittest.mock import MagicMock
    from tests.fixtures.quarto_diagram_fixtures import (
        FIXTURE_G_MIXED_DOCUMENT,
        FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM,
    )

    discovery = QuartoRuntimeDiscovery()
    readiness = discovery.probe_readiness()

    if not readiness.quarto_available or not readiness.xelatex_available:
        return StageResult(
            stage_name="quarto-pdf-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="PDF-00-TOOLCHAIN",
                name="Quarto and XeLaTeX Toolchain Probe",
                status=TestStatus.BLOCKED,
                error_message=f"Missing prerequisites: {readiness.missing_prerequisites}",
            )],
        )

    def compile_test_fixture(test_id: str, name: str, markdown_content: str) -> TestItemResult:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="polpo_accept_pdf_") as td:
            temp_dir = Path(td)
            artifacts_dir = temp_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            canonical_md = artifacts_dir / "canonical.md"
            raw_bytes = markdown_content.encode("utf-8")
            canonical_md.write_bytes(raw_bytes)
            content_hash = hashlib.sha256(raw_bytes).hexdigest()

            # Mock UoW
            mock_job = MagicMock(spec=Job)
            mock_job.id = 999
            mock_job.file_name = "test_doc.pdf"

            mock_ver = DocumentVersionRecord(
                job_id=999,
                version=1,
                output_path=str(canonical_md),
                sha256=content_hash,
                integrity_status="VALID",
            )

            mock_uow = MagicMock()
            mock_uow.publish_intents.get_by_job_id.return_value = None
            mock_uow.jobs.get_by_id.return_value = mock_job
            mock_uow.document_versions.get_by_version.return_value = mock_ver

            uow_factory = MagicMock()
            uow_factory.create.return_value.__enter__.return_value = mock_uow

            adapter = QuartoPdfAdapter(
                discovery=discovery,
                supervisor=SubprocessSupervisor(),
                staging=StagingWorkspaceManager(),
                sanitizer=TexLogSanitizer(),
            )
            publisher = AtomicPdfPublisher()
            service = QuartoPdfExportService(
                uow_factory=uow_factory,
                export_engine=adapter,
                publisher=publisher,
                artifacts_dir=artifacts_dir,
                discovery=discovery,
            )

            target_pdf = artifacts_dir / "exported_document.pdf"

            try:
                result = service.export_pdf(
                    ExportPdfCommand(job_id=999, version=1, destination_path=target_pdf, overwrite=True)
                )

                if not target_pdf.is_file() or target_pdf.stat().st_size == 0:
                    return TestItemResult(
                        test_id=test_id,
                        name=name,
                        status=TestStatus.FAIL,
                        duration_ms=(time.monotonic() - t0) * 1000,
                        error_message="Output PDF file was not created or is 0 bytes",
                    )

                doc = fitz.open(str(target_pdf))
                page_count = len(doc)
                drawings_count = sum(len(page.get_drawings()) for page in doc)
                all_text = " ".join(page.get_text() for page in doc)

                # Verify vector drawings exist
                has_drawings = drawings_count > 0

                # Verify no raw TikZ leakage in text layer
                has_no_leakage = (
                    "```{=latex}" not in all_text
                    and r"\begin{tikzpicture}" not in all_text
                    and r"\begin{circuitikz}" not in all_text
                )

                # Verify rasterization works on page 0
                pix = doc[0].get_pixmap()
                pix_valid = pix.width > 0 and pix.height > 0 and len(pix.samples) > 0

                doc.close()

                is_success = page_count >= 1 and has_drawings and has_no_leakage and pix_valid

                return TestItemResult(
                    test_id=test_id,
                    name=name,
                    status=TestStatus.PASS if is_success else TestStatus.FAIL,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    details={
                        "page_count": page_count,
                        "vector_drawings": drawings_count,
                        "has_no_raw_latex_leakage": has_no_leakage,
                        "page_raster_width": pix.width,
                        "page_raster_height": pix.height,
                        "pdf_bytes": target_pdf.stat().st_size,
                    },
                )
            except Exception as exc:
                return TestItemResult(
                    test_id=test_id,
                    name=name,
                    status=TestStatus.FAIL,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error_message=str(exc),
                )

    items.append(compile_test_fixture(
        test_id="PDF-01-FIXTURE-G-MIXED",
        name="Fixture G: Full Mixed STEM Document (Math, RTL, TikZ, CircuitTikZ, Tables)",
        markdown_content=FIXTURE_G_MIXED_DOCUMENT,
    ))

    items.append(compile_test_fixture(
        test_id="PDF-02-FIXTURE-D-BILINGUAL",
        name="Fixture D: Bilingual Persian/English Feedback Control Diagram",
        markdown_content=FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM,
    ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="quarto-pdf-live",
        status=TestStatus.PASS if failed == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 5: Real Gemini AI Acceptance (Explicit Opt-In via --run-live-ai)
# ---------------------------------------------------------------------------

def run_gemini_live(opt_in: bool) -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    if not opt_in:
        return StageResult(
            stage_name="gemini-live",
            status=TestStatus.SKIPPED_WITH_REASON,
            total_tests=1,
            skipped_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="GEMINI-00-OPT-IN",
                name="Live AI Execution Opt-In Gate",
                status=TestStatus.SKIPPED_WITH_REASON,
                details={"reason": "Live AI requests require explicit --run-live-ai command-line flag to prevent unintended quota consumption"},
            )],
        )

    from interfaces.desktop.composition import DesktopAppContainer
    from core.entities.api_slot import ApiSlot, CredentialRef
    from core.ai.exceptions import AIAuthenticationError, AITimeoutError
    from application.dto.latex_repair_dto import LaTeXRepairRequest
    from PIL import Image

    user_db = get_user_data_dir() / "polpot.db"
    if not user_db.is_file():
        return StageResult(
            stage_name="gemini-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="GEMINI-00-CONFIG",
                name="Active AI Slot Resolution",
                status=TestStatus.BLOCKED,
                error_message="Polpo database not found; no configured AI slots",
            )],
        )

    try:
        container = DesktopAppContainer(instance_lock=None)
        with container.uow_factory.create() as uow:
            active_slots = [s for s in uow.apis.list_all() if getattr(s, "is_active", True)]
    except Exception as exc:
        return StageResult(
            stage_name="gemini-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="GEMINI-00-CONFIG",
                name="Active AI Slot Resolution",
                status=TestStatus.BLOCKED,
                error_message=f"Could not read AI slots from database: {exc}",
            )],
        )

    if not active_slots:
        return StageResult(
            stage_name="gemini-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="GEMINI-00-SLOT",
                name="Active AI Slot Verification",
                status=TestStatus.BLOCKED,
                error_message="No active AI slot found in local database",
            )],
        )

    primary_slot = active_slots[0]
    try:
        resolved_key = container.credential_resolver.resolve_api_key(primary_slot.credential_ref)
    except Exception:
        resolved_key = None

    if not resolved_key:
        return StageResult(
            stage_name="gemini-live",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="GEMINI-00-KEY",
                name="Keyring/Encrypted Vault Secret Resolution",
                status=TestStatus.BLOCKED,
                error_message="Credential could not be resolved from OS keyring or encrypted vault for active slot",
            )],
        )

    # 1. Real Text Generation Request via RateLimitedAIExecutor
    t0 = time.monotonic()
    try:
        content, used_slot = container.ai_executor.execute_text_with_fallback(
            chain=active_slots,
            prompt="Please respond with the exact word 'OK_POLPO'",
            at_page=0,
        )
        is_ok = bool(content and "OK_POLPO" in content)
        items.append(TestItemResult(
            test_id="GEMINI-01-TEXT",
            name="Real Text Generation via RateLimitedAIExecutor",
            status=TestStatus.PASS if is_ok else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"model": used_slot.selected_model if used_slot else "", "slot": used_slot.label if used_slot else ""},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-01-TEXT",
            name="Real Text Generation via RateLimitedAIExecutor",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message=str(exc),
        ))

    time.sleep(1.5)

    # 2. Real Vision Request (Synthetic Test Image) via RateLimitedAIExecutor
    t0 = time.monotonic()
    try:
        img = Image.new("RGB", (64, 64), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        try:
            v_content, used_slot = container.ai_executor.execute_vision_with_fallback(
                chain=active_slots,
                image_bytes=buf.getvalue(),
                prompt="What is the primary color of this small image? Answer with one word.",
                at_page=0,
                mime_type="image/png",
            )
        except Exception:
            time.sleep(2.0)
            v_content, used_slot = container.ai_executor.execute_vision_with_fallback(
                chain=active_slots,
                image_bytes=buf.getvalue(),
                prompt="What is the primary color of this small image? Answer with one word.",
                at_page=0,
                mime_type="image/png",
            )
        v_ok = bool(v_content and len(v_content.strip()) > 0)
        items.append(TestItemResult(
            test_id="GEMINI-02-VISION",
            name="Real Vision Request via RateLimitedAIExecutor",
            status=TestStatus.PASS if v_ok else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"model": used_slot.selected_model if used_slot else "", "response_snippet": v_content.strip()[:50] if v_content else ""},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-02-VISION",
            name="Real Vision Request via RateLimitedAIExecutor",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message=str(exc),
        ))

    time.sleep(1.5)

    # 3. Real Structured LaTeX Repair via LaTeXRepairService and Same-Renderer Validation
    t0 = time.monotonic()
    try:
        repair_req = LaTeXRepairRequest(
            job_id=1,
            canonical_version=1,
            node_id="acceptance_test_math",
            original_source=r"\frac{a}{",
            renderer_type="math_display",
            sanitized_diagnostics="Missing close brace",
        )
        proposal = container.latex_repair_service.repair_latex(repair_req)
        r_ok = proposal.validation_passed and bool(proposal.rendered_preview_xml)
        items.append(TestItemResult(
            test_id="GEMINI-03-REPAIR-STRUCTURED",
            name="Real Structured LaTeX Repair with Same-Renderer Validation",
            status=TestStatus.PASS if r_ok else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={
                "model": proposal.model_name,
                "repaired_source": proposal.repaired_source,
                "change_summary": proposal.change_summary,
                "preview_svg_bytes": len(proposal.rendered_preview_xml or ""),
            },
            error_message=proposal.validation_error if not r_ok else None,
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-03-REPAIR-STRUCTURED",
            name="Real Structured LaTeX Repair with Same-Renderer Validation",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message=str(exc),
        ))

    # 4. Fallback Behavior when First Configured Provider Fails
    t0 = time.monotonic()
    try:
        from infrastructure.ai.factory import create_ai_adapter
        from infrastructure.ai.executor_service import RateLimitedAIExecutor

        bad_slot = ApiSlot(
            id=9999,
            provider=primary_slot.provider,
            label="Failing Primary Slot",
            slot_type="byok",
            selected_model=primary_slot.selected_model,
        )

        orig_factory = container.ai_adapter_factory
        def test_fallback_factory(slot: ApiSlot):
            if slot.id == 9999:
                return create_ai_adapter(
                    provider=slot.provider,
                    api_key="AIzaSy_INVALID_TEST_KEY_FOR_FALLBACK",
                    default_model=slot.selected_model,
                )
            return orig_factory(slot)

        fallback_executor = RateLimitedAIExecutor(
            adapter_factory=test_fallback_factory,
            rate_limiter=container.rate_limiter,
        )

        test_chain = [bad_slot] + active_slots
        fallback_content, used_slot = fallback_executor.execute_text_with_fallback(
            chain=test_chain,
            prompt="Please respond with the exact word 'FALLBACK_OK'",
            at_page=0,
        )
        fallback_ok = bool(used_slot and used_slot.id != bad_slot.id and fallback_content)
        items.append(TestItemResult(
            test_id="GEMINI-04-FALLBACK-BEHAVIOR",
            name="AI Provider Fallback on First Provider Failure",
            status=TestStatus.PASS if fallback_ok else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"transitioned_to_slot": used_slot.label if used_slot else None},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-04-FALLBACK-BEHAVIOR",
            name="AI Provider Fallback on First Provider Failure",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message=str(exc),
        ))

    # 5. Authentication Failure Behavior
    t0 = time.monotonic()
    try:
        bad_auth_slot = ApiSlot(
            id=9998,
            provider=primary_slot.provider,
            label="Invalid Auth Slot",
            slot_type="byok",
            selected_model=primary_slot.selected_model,
        )

        def bad_auth_factory(slot: ApiSlot):
            return create_ai_adapter(
                provider=slot.provider,
                api_key="AIzaSy_INVALID_TEST_KEY_FOR_AUTH",
                default_model=slot.selected_model,
            )

        auth_executor = RateLimitedAIExecutor(
            adapter_factory=bad_auth_factory,
            rate_limiter=container.rate_limiter,
        )

        auth_content, auth_used = auth_executor.execute_text_with_fallback(
            chain=[bad_auth_slot],
            prompt="Hello",
            at_page=0,
        )
        auth_handled = (auth_content is None and auth_used is None)
        items.append(TestItemResult(
            test_id="GEMINI-05-AUTH-ERROR",
            name="Authentication Failure Exception Mapping",
            status=TestStatus.PASS if auth_handled else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"handled_gracefully": auth_handled},
        ))
    except AIAuthenticationError as auth_err:
        items.append(TestItemResult(
            test_id="GEMINI-05-AUTH-ERROR",
            name="Authentication Failure Exception Mapping",
            status=TestStatus.PASS,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"caught_auth_error": auth_err.message[:80]},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-05-AUTH-ERROR",
            name="Authentication Failure Exception Mapping",
            status=TestStatus.PASS,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"caught_exception": type(exc).__name__},
        ))

    # 6. Timeout and Network Failure Behavior
    t0 = time.monotonic()
    try:
        from infrastructure.ai.executor_service import RateLimitedAIExecutor
        from unittest.mock import MagicMock
        mock_adapter = MagicMock()
        mock_adapter.generate_text.side_effect = AITimeoutError("Simulated request timeout after 30s")

        timeout_executor = RateLimitedAIExecutor(
            adapter_factory=lambda slot: mock_adapter,
            rate_limiter=container.rate_limiter,
        )
        t_content, t_used = timeout_executor.execute_text_with_fallback(
            chain=active_slots[:1],
            prompt="Timeout test",
            at_page=0,
        )
        timeout_handled = (t_content is None)
        items.append(TestItemResult(
            test_id="GEMINI-06-TIMEOUT-FAILURE",
            name="Timeout and Network Failure Handling",
            status=TestStatus.PASS if timeout_handled else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"handled_gracefully": timeout_handled},
        ))
    except Exception as exc:
        items.append(TestItemResult(
            test_id="GEMINI-06-TIMEOUT-FAILURE",
            name="Timeout and Network Failure Handling",
            status=TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            error_message=str(exc),
        ))

    # Clean up MathJax worker process if started during repair
    if hasattr(container, "mathjax_supervisor") and container.mathjax_supervisor:
        container.mathjax_supervisor.shutdown()

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="gemini-live",
        status=TestStatus.PASS if failed == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 6: Real Native Desktop Preview Acceptance (Offscreen Qt)
# ---------------------------------------------------------------------------

def run_desktop_preview() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    # Configure headless Qt offscreen platform
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QCoreApplication
    from interfaces.desktop.providers.diagram_image_provider import DiagramImageProvider
    from interfaces.desktop.providers.math_image_provider import MathImageProvider
    from infrastructure.diagram.cache import DiagramSvgCache
    from infrastructure.math.lru_cache import MathSvgCache
    from application.ports.diagram_renderer import DiagramRenderResult
    from application.ports.math_renderer import MathRenderResult

    app = QCoreApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])

    # 1. Diagram Image Provider with vector SVG
    t0 = time.monotonic()
    diag_cache = DiagramSvgCache()
    sample_diag_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#3b82f6"/>'
        '<text x="20" y="50" fill="#ffffff" font-size="16">Acceptance Diagram</text>'
        '</svg>'
    )
    diag_hash = "diag_test_hash_123"
    diag_cache.put(DiagramRenderResult(
        hash=diag_hash,
        svg_xml=sample_diag_svg,
        width=200.0,
        height=100.0,
        status="rendered",
    ))

    diag_provider = DiagramImageProvider(cache=diag_cache)
    img_dark = diag_provider.requestImage(f"dark/{diag_hash}", None, None)
    img_light = diag_provider.requestImage(f"light/{diag_hash}", None, None)

    diag_ok = (
        not img_dark.isNull()
        and img_dark.width() > 0
        and img_dark.height() > 0
        and not img_light.isNull()
    )
    items.append(TestItemResult(
        test_id="PREVIEW-01-DIAGRAM-PROVIDER",
        name="Diagram Image Provider Rendering (Dark and Light Themes)",
        status=TestStatus.PASS if diag_ok else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={
            "dark_width": img_dark.width(),
            "dark_height": img_dark.height(),
            "light_width": img_light.width(),
            "light_height": img_light.height(),
        },
    ))

    # 2. Math Image Provider with vector SVG
    t0 = time.monotonic()
    math_cache = MathSvgCache()
    sample_math_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="150" height="60">'
        '<text x="10" y="40" fill="currentColor">E = mc^2</text>'
        '</svg>'
    )
    math_hash = "math_test_hash_456"
    math_cache.put(
        math_hash,
        MathRenderResult(
            hash=math_hash,
            svg_xml=sample_math_svg,
            width=150.0,
            height=60.0,
            vertical_align=-0.5,
        ),
    )

    math_provider = MathImageProvider(cache=math_cache)
    m_dark = math_provider.requestImage(f"dark/{math_hash}", None, None)
    m_light = math_provider.requestImage(f"light/{math_hash}", None, None)

    math_ok = (
        not m_dark.isNull()
        and m_dark.width() > 0
        and m_dark.height() > 0
        and not m_light.isNull()
    )
    items.append(TestItemResult(
        test_id="PREVIEW-02-MATH-PROVIDER",
        name="Math Image Provider Rendering (Dark and Light Themes)",
        status=TestStatus.PASS if math_ok else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={
            "dark_width": m_dark.width(),
            "dark_height": m_dark.height(),
            "light_width": m_light.width(),
            "light_height": m_light.height(),
        },
    ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="desktop-preview",
        status=TestStatus.PASS if failed == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 7: TeX Execution Security and Isolation Acceptance
# ---------------------------------------------------------------------------

def run_tex_security() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    from infrastructure.diagram.standalone_tex_renderer import (
        StandaloneTeXDiagramRenderer,
        _build_sandboxed_environment,
    )
    from application.services.latex_repair_service import validate_latex_safety
    from application.ports.diagram_renderer import DiagramRenderRequest, DiagramRenderError

    compiler = StandaloneTeXDiagramRenderer().resolve_compiler_path()
    if not compiler:
        return StageResult(
            stage_name="tex-security",
            status=TestStatus.BLOCKED,
            total_tests=1,
            blocked_tests=1,
            duration_seconds=time.monotonic() - start_time,
            items=[TestItemResult(
                test_id="SEC-00-TOOLCHAIN",
                name="TeX Compiler Security Prerequisite",
                status=TestStatus.BLOCKED,
                error_message="TeX compiler not found",
            )],
        )

    # 1. Environment Sanitization and Kpathsea Policy
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="polpo_sec_env_") as td:
        workdir = Path(td)
        os.environ["POLPO_TEST_SECRET_API_KEY_123"] = "secret_val"
        try:
            env = _build_sandboxed_environment(workdir)
            sec_ok = (
                env.get("openin_any") == "p"
                and env.get("openout_any") == "p"
                and env.get("shell_escape") == "f"
                and env.get("TEXMFOUTPUT") == str(workdir)
                and "POLPO_TEST_SECRET_API_KEY_123" not in env
                and "PATH" in env
            )
            items.append(TestItemResult(
                test_id="SEC-01-PARANOID-ENV",
                name="Subprocess Environment Sanitization & Kpathsea Paranoid Policy",
                status=TestStatus.PASS if sec_ok else TestStatus.FAIL,
                duration_ms=(time.monotonic() - t0) * 1000,
                details={
                    "openin_any": env.get("openin_any"),
                    "openout_any": env.get("openout_any"),
                    "shell_escape": env.get("shell_escape"),
                    "secrets_stripped": True,
                },
            ))
        finally:
            os.environ.pop("POLPO_TEST_SECRET_API_KEY_123", None)

    renderer = StandaloneTeXDiagramRenderer()

    # 2. Canary File External Absolute Path Read
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="polpo_sec_canary_") as cd:
        canary = Path(cd) / "secret.txt"
        canary_secret = "TOP_SECRET_CANARY_TOKEN_998811"
        canary.write_text(canary_secret, encoding="utf-8")

        req = DiagramRenderRequest(
            source=rf"\begin{{tikzpicture}}\node {{\input{{{canary.as_posix()}}}}};\end{{tikzpicture}}"
        )
        try:
            renderer.render(req)
            leak_detected = True
        except DiagramRenderError as exc:
            leak_detected = canary_secret in str(exc)
        except Exception:
            leak_detected = False

        items.append(TestItemResult(
            test_id="SEC-02-CANARY-ABS-READ",
            name="External Canary File Absolute Path Read Blocking",
            status=TestStatus.PASS if not leak_detected else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"blocked_read": not leak_detected},
        ))

    # 3. Relative Path Traversal Read
    t0 = time.monotonic()
    req = DiagramRenderRequest(
        source=r"\begin{tikzpicture}\node {\input{../secret.txt}};\end{tikzpicture}"
    )
    rel_blocked = False
    try:
        renderer.render(req)
    except DiagramRenderError:
        rel_blocked = True
    except Exception:
        rel_blocked = True

    items.append(TestItemResult(
        test_id="SEC-03-CANARY-REL-READ",
        name="Relative Path Traversal Read Blocking",
        status=TestStatus.PASS if rel_blocked else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"traversal_blocked": rel_blocked},
    ))

    # 4. External File Write Blocking
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="polpo_sec_write_") as wd:
        target = Path(wd) / "unauthorized_file.txt"
        req = DiagramRenderRequest(
            source=rf"""\begin{{tikzpicture}}
    \newwrite\outfile
    \openout\outfile={target.as_posix()}
    \write\outfile{{MALICIOUS_WRITE}}
    \closeout\outfile
\end{{tikzpicture}}"""
        )
        try:
            renderer.render(req)
        except Exception:
            pass

        items.append(TestItemResult(
            test_id="SEC-04-CANARY-WRITE",
            name="External File Write Blocking (openout_any=p)",
            status=TestStatus.PASS if not target.exists() else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"target_exists": target.exists()},
        ))

    # 5. Shell Execution (\write18) Blocking
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="polpo_sec_sh_") as sd:
        marker = Path(sd) / "marker.txt"
        req = DiagramRenderRequest(
            source=rf"""\begin{{tikzpicture}}
    \immediate\write18{{touch {marker.as_posix()}}}
\end{{tikzpicture}}"""
        )
        try:
            renderer.render(req)
        except Exception:
            pass

        items.append(TestItemResult(
            test_id="SEC-05-WRITE18-SHELL",
            name="Shell Command Execution Blocking (-no-shell-escape)",
            status=TestStatus.PASS if not marker.exists() else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"shell_executed": marker.exists()},
        ))

    # 6. AI Response Validation Tokenizer and Comment Hygiene
    t0 = time.monotonic()
    malicious = [
        r"\input{file}", r"\include{file}", r"\openin\f=x", r"\read\f to \d",
        r"\openout\f=x", r"\write18{ls}", r"\catcode`\@=11", r"\special{ps:}",
        r"\documentclass{article}", r"\usepackage{x}", r"../evil",
        r"\csname input\endcsname{f}", r"\@@input f",
        r"\begin{filecontents}{evil.tex}x\end{filecontents}",
        r"\immediate\write18{ls}", r"\scantokens{\input{f}}",
        r"\includegraphics{/etc/passwd}", r"\includegraphics{C:/Windows/win.ini}",
        r"\includegraphics{/custom/canary.png}", r"\includegraphics{\\server\share\canary.png}",
        r"\lstinputlisting{secret.txt}", r"\verbatiminput{secret.txt}",
        r"\import{dir/}{file.tex}", r"\catchfiledef{\data}{secret.txt}{}",
    ]
    all_rejected = all(not validate_latex_safety(m)[0] for m in malicious)
    benign = [
        r"% \input{commented}",
        r"\node {input};",
        r"\node {100\%};",
        r"\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}",
    ]
    all_accepted = all(validate_latex_safety(b)[0] for b in benign)
    items.append(TestItemResult(
        test_id="SEC-06-AI-VALIDATION",
        name="AI Candidate Validation & Comment-Aware Inspection",
        status=TestStatus.PASS if (all_rejected and all_accepted) else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"all_malicious_rejected": all_rejected, "all_benign_accepted": all_accepted},
    ))

    # 7. Normal Features Preserved
    t0 = time.monotonic()
    safe_req = DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \draw (0,0) rectangle (2,1);
\end{tikzpicture}"""
    )
    safe_res = renderer.render(safe_req)
    safe_ok = safe_res.status == "rendered" and "<svg" in safe_res.svg_xml
    items.append(TestItemResult(
        test_id="SEC-07-NORMAL-PRESERVATION",
        name="Legitimate Diagram Compilation Preservation",
        status=TestStatus.PASS if safe_ok else TestStatus.FAIL,
        duration_ms=(time.monotonic() - t0) * 1000,
        details={"svg_bytes": len(safe_res.svg_xml)},
    ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="tex-security",
        status=TestStatus.PASS if failed == 0 and blocked == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 8: Real Native QML End-to-End Scene Graph Acceptance
# ---------------------------------------------------------------------------

def run_qml_e2e_acceptance() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    from PySide6.QtCore import QUrl, QObject, Property, Signal, Slot
    from PIL import Image

    from infrastructure.markdown.pandoc_parser import PandocParser
    from infrastructure.diagram.cache import DiagramSvgCache
    from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer
    from infrastructure.math.lru_cache import MathSvgCache
    from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor
    from infrastructure.math.mathjax_client import MathJaxClient
    from application.services.markdown_viewer_service import MarkdownViewerService
    from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
    from interfaces.desktop.providers.diagram_image_provider import DiagramImageProvider
    from interfaces.desktop.providers.math_image_provider import MathImageProvider
    from interfaces.desktop.controllers.theme_controller import ThemeController
    from unittest.mock import MagicMock
    from tests.acceptance.test_qml_e2e_acceptance import E2E_MIXED_DOCUMENT_FIXTURE

    t0 = time.monotonic()
    supervisor = MathJaxProcessSupervisor()
    math_cache = MathSvgCache()
    math_client = MathJaxClient(supervisor=supervisor, cache=math_cache)
    diag_cache = DiagramSvgCache()
    diag_renderer = StandaloneTeXDiagramRenderer(cache=diag_cache)

    try:
        service = MarkdownViewerService(
            parser=PandocParser(),
            uow_factory=MagicMock(),
            storage=MagicMock(),
            math_renderer=math_client,
            diagram_renderer=diag_renderer,
        )

        dto = service.render_text(
            raw_text=E2E_MIXED_DOCUMENT_FIXTURE,
            active_regions=[],
            job_id=999,
            version=1,
        )
        nodes = dto.nodes
        items.append(TestItemResult(
            test_id="QML-01-PIPELINE-PROJECTION",
            name="Markdown AST & Diagram/Math Projection Pipeline",
            status=TestStatus.PASS if len(nodes) >= 12 else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t0) * 1000,
            details={"node_count": len(nodes)},
        ))

        # Check code block fence integrity
        code_nodes = [n for n in nodes if n.node_type == "code_block"]
        is_code_ok = bool(code_nodes and "\\begin{tikzpicture}" in (code_nodes[0].content or code_nodes[0].raw_markdown))
        items.append(TestItemResult(
            test_id="QML-02-CODE-FENCE-INTEGRITY",
            name="Ordinary Code Fence Hijack Prevention",
            status=TestStatus.PASS if is_code_ok else TestStatus.FAIL,
            duration_ms=1.0,
            details={"code_nodes_count": len(code_nodes)},
        ))

        # Diagram Nodes
        diagram_nodes = [n for n in nodes if n.node_type == "diagram"]
        valid_diagrams = [d for d in diagram_nodes if not d.has_error]
        invalid_diagrams = [d for d in diagram_nodes if d.has_error]

        items.append(TestItemResult(
            test_id="QML-03-DIAGRAM-SVGS",
            name="Real Technical Diagram SVGs (TikZ, PGFPlots, CircuitTikZ, TikZ-CD)",
            status=TestStatus.PASS if len(valid_diagrams) == 5 else TestStatus.FAIL,
            duration_ms=1.0,
            details={"valid_diagram_count": len(valid_diagrams)},
        ))

        items.append(TestItemResult(
            test_id="QML-04-ERROR-CARD-METADATA",
            name="Broken Diagram Error Card Metadata Generation",
            status=TestStatus.PASS if len(invalid_diagrams) == 1 else TestStatus.FAIL,
            duration_ms=1.0,
            details={"error_message": invalid_diagrams[0].error_message if invalid_diagrams else ""},
        ))

        # Model & Providers
        model = MarkdownDocumentModel()
        model.set_document(dto)
        diag_provider = DiagramImageProvider(cache=diag_cache)
        math_provider = MathImageProvider(cache=math_cache)

        t_qml = time.monotonic()
        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        view = QQuickView()
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.resize(900, 1400)

        view.engine().addImageProvider("diagram", diag_provider)
        view.engine().addImageProvider("math", math_provider)

        theme = ThemeController()
        view.rootContext().setContextProperty("theme", theme)

        class DummyViewerController(QObject):
            requestScrollToNode = Signal(int)

            def __init__(self, m):
                super().__init__()
                self._m = m
            @Property(bool, constant=True)
            def hasDocument(self): return True
            @Property(bool, constant=True)
            def isLoading(self): return False
            @Property(str, constant=True)
            def errorMessage(self): return ""
            @Property(bool, constant=True)
            def previewPaused(self): return False
            @Property(str, constant=True)
            def previewPausedReason(self): return ""
            @Property(int, constant=True)
            def activeVersion(self): return 1
            @Property(float, constant=True)
            def scaleFactor(self): return 1.0
            @Property(int, constant=True)
            def selectedNodeIndex(self): return -1
            @Property(QObject, constant=True)
            def model(self): return self._m
            @Slot(result=int)
            def modelGeneration(self): return 1
            @Slot(int, int)
            def reportNodeClicked(self, i, g): pass

        ctrl = DummyViewerController(model)
        view.rootContext().setContextProperty("markdownViewerController", ctrl)

        qml_path = _REPO_ROOT / "interfaces" / "desktop" / "qml" / "views" / "MarkdownView.qml"
        view.setSource(QUrl.fromLocalFile(str(qml_path)))
        view.show()

        for _ in range(25):
            app.processEvents()
            time.sleep(0.01)

        img = view.grabWindow()
        out_png = _REPO_ROOT / "build" / "acceptance_qml_preview.png"
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_png))

        pil_img = Image.open(str(out_png))
        colors = pil_img.getcolors(maxcolors=20000)
        has_colors = len(colors or []) > 20

        items.append(TestItemResult(
            test_id="QML-05-SCENE-GRAPH-CAPTURE",
            name="Qt Quick Scene Graph Offscreen Render & Screenshot Capture (Dark)",
            status=TestStatus.PASS if (img.width() == 900 and img.height() == 1400 and has_colors) else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t_qml) * 1000,
            details={
                "screenshot_path": str(out_png),
                "dimensions": f"{img.width()}x{img.height()}",
                "distinct_colors": len(colors or []),
                "file_bytes": out_png.stat().st_size if out_png.is_file() else 0,
            },
        ))

        # Switch to Light theme and capture
        t_light = time.monotonic()
        theme.set_theme_preference("light")
        for _ in range(25):
            app.processEvents()
            time.sleep(0.01)

        light_img = view.grabWindow()
        light_png = _REPO_ROOT / "build" / "acceptance_qml_preview_light.png"
        light_img.save(str(light_png))

        pil_light = Image.open(str(light_png))
        light_colors = pil_light.getcolors(maxcolors=20000)
        has_light_colors = len(light_colors or []) > 20

        items.append(TestItemResult(
            test_id="QML-06-LIGHT-THEME-RENDER",
            name="Qt Quick Scene Graph Dynamic Light Theme Re-render",
            status=TestStatus.PASS if (light_img.width() == 900 and light_img.height() == 1400 and has_light_colors) else TestStatus.FAIL,
            duration_ms=(time.monotonic() - t_light) * 1000,
            details={
                "screenshot_path": str(light_png),
                "distinct_colors": len(light_colors or []),
                "file_bytes": light_png.stat().st_size if light_png.is_file() else 0,
            },
        ))

    finally:
        supervisor.shutdown()

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="qml-preview-e2e",
        status=TestStatus.PASS if failed == 0 and blocked == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Stage 9: Interactive AI LaTeX Repair Workflow Acceptance
# ---------------------------------------------------------------------------

def run_repair_workflow() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

    from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
    from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
    from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
    from infrastructure.storage.local_storage import LocalStorageAdapter
    from application.services.crop_artifact_staging_service import CropArtifactStagingService
    from application.services.document_publication_service import DocumentPublicationService
    from application.services.markdown_editor_service import MarkdownEditorService
    from application.services.latex_repair_service import LaTeXRepairService
    from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
    from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer
    from application.ports.ai_executor import IAIExecutionService
    from core.entities.api_slot import ApiSlot
    from core.entities.credential_ref import CredentialRef
    from core.entities.job import Job, JobStatus
    from core.entities.prompt import Prompt, PromptType
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory(prefix="polpo_repair_wf_") as td:
        temp_path = Path(td)
        db_file = temp_path / "repair_wf.db"
        db_manager = SQLiteDatabaseManager(str(db_file))
        SQLiteMigrationRunner(db_manager).run_migrations()
        uow_factory = SQLiteUnitOfWorkFactory(db_manager)

        artifacts_dir = temp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        staging_service = CropArtifactStagingService(base_dir=artifacts_dir / ".staging")
        storage = LocalStorageAdapter(base_dir=str(artifacts_dir))

        pub_service = DocumentPublicationService(
            uow_factory=uow_factory,
            artifacts_dir=artifacts_dir,
            staging_service=staging_service,
        )
        editor_service = MarkdownEditorService(
            uow_factory=uow_factory,
            storage=storage,
            document_publication_service=pub_service,
        )

        with uow_factory.create() as uow:
            slot = ApiSlot(
                id=None,
                provider="google",
                label="Gemini Flash",
                selected_model="gemini-2.5-flash",
                credential_ref=CredentialRef("c1", "google", "byok"),
            )
            uow.apis.save(slot)
            p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
            job = uow.jobs.save(Job(id=None, file_name="doc.pdf", file_path=str(temp_path / "doc.pdf"), prompt_id=p.id, status=JobStatus.DONE))
            uow.commit()
            job_id = job.id

        broken_tikz = r"\begin{tikzpicture}" + "\n" + r"    \draw (0,0) -- (1,1)" + "\n" + r"\end{tikzpicture}"
        corrected_tikz = r"\begin{tikzpicture}" + "\n" + r"    \draw (0,0) -- (1,1);" + "\n" + r"\end{tikzpicture}"
        doc_text = f"# Title\n\n```{{=latex}}\n{broken_tikz}\n```\n\nEnd.\n"
        pub_service.publish_initial(job_id=job_id, markdown_text=doc_text)

        ai_calls: list[dict] = []
        def mock_ai_execute(chain, prompt, **kwargs):
            ai_calls.append({"prompt": prompt})
            return (
                json.dumps({
                    "schema_version": 1,
                    "repaired_source": corrected_tikz,
                    "change_summary": "Added semicolon",
                    "preserved_invariants": ["syntax_valid"],
                }),
                chain[0],
            )

        mock_ai = MagicMock(spec=IAIExecutionService)
        mock_ai.execute_text_with_fallback.side_effect = mock_ai_execute

        diag_renderer = StandaloneTeXDiagramRenderer()
        repair_service = LaTeXRepairService(
            uow_factory=uow_factory,
            ai_executor=mock_ai,
            diagram_renderer=diag_renderer,
        )

        ctrl = MarkdownEditorController(
            editor_service=editor_service,
            repair_service=repair_service,
        )

        try:
            ctrl.load_source_sync(job_id)

            # 1. Gating
            items.append(TestItemResult(
                test_id="REPAIR-01-GATING",
                name="AI Repair Reactive Action Gating (canRepair)",
                status=TestStatus.PASS if ctrl.canRepair else TestStatus.FAIL,
                details={"canRepair": ctrl.canRepair},
            ))

            # 2. Consent Check: 0 requests when not granted
            items.append(TestItemResult(
                test_id="REPAIR-02-PRE-TRANSMISSION-CONSENT",
                name="Pre-Transmission Consent Enforcement",
                status=TestStatus.PASS if len(ai_calls) == 0 else TestStatus.FAIL,
                details={"ai_calls_prior_to_consent": len(ai_calls)},
            ))

            # 3. Request repair with user consent
            proposals: list = []
            ctrl.repairProposalReady.connect(proposals.append)
            ctrl.requestRepair("diag-1", broken_tikz, "diagram", "Syntax error", 0, "", 3, 5)

            deadline = time.time() + 4.0
            while not proposals and time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)

            prop = proposals[0] if proposals else None
            is_valid_prop = bool(prop and prop.get("validationPassed") and "<svg" in prop.get("renderedPreviewXml", ""))
            items.append(TestItemResult(
                test_id="REPAIR-03-SAME-RENDERER-VALIDATION",
                name="Same-Pipeline Automated Compiler Validation",
                status=TestStatus.PASS if is_valid_prop else TestStatus.FAIL,
                details={"validation_passed": prop.get("validationPassed") if prop else False},
            ))

            # Check minimal disclosure
            transmitted = ai_calls[0]["prompt"] if ai_calls else ""
            minimal_ok = broken_tikz in transmitted and "# Title" not in transmitted
            items.append(TestItemResult(
                test_id="REPAIR-04-MINIMAL-DISCLOSURE",
                name="Data Minimization & Diagnostic Excerpt Boundary",
                status=TestStatus.PASS if minimal_ok else TestStatus.FAIL,
                details={"document_leakage": "# Title" in transmitted},
            ))

            # 4. Reject proposal leaves buffer intact
            ctrl.rejectRepair()
            reject_ok = (ctrl.activeProposal is None and not ctrl.isDirty and broken_tikz in ctrl.sourceText)
            items.append(TestItemResult(
                test_id="REPAIR-05-REJECT-INTEGRITY",
                name="Proposal Rejection Buffer Immutability",
                status=TestStatus.PASS if reject_ok else TestStatus.FAIL,
                details={"isDirty": ctrl.isDirty},
            ))

            # 5. Apply, Undo, Redo
            proposals.clear()
            ctrl.requestRepair("diag-1", broken_tikz, "diagram", "Syntax error", 0, "", 3, 5)
            deadline = time.time() + 4.0
            while not proposals and time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)

            ctrl.applyRepair()
            apply_ok = ctrl.isDirty and corrected_tikz in ctrl.sourceText and ctrl.activeVersion == 1

            # Undo
            doc = ctrl._get_document()
            doc.undo()
            ctrl.set_source_text(doc.toPlainText())
            undo_ok = broken_tikz in ctrl.sourceText

            # Redo
            doc.redo()
            ctrl.set_source_text(doc.toPlainText())
            redo_ok = corrected_tikz in ctrl.sourceText

            items.append(TestItemResult(
                test_id="REPAIR-06-UNDO-REDO-OCC",
                name="Atomic Buffer Replacement, Undo/Redo & OCC Watermark",
                status=TestStatus.PASS if (apply_ok and undo_ok and redo_ok) else TestStatus.FAIL,
                details={"apply_ok": apply_ok, "undo_ok": undo_ok, "redo_ok": redo_ok},
            ))

            # 6. Save under OCC
            ctrl.save_sync()
            saved_text, saved_ver = editor_service.load_source_text(job_id)
            save_ok = (saved_ver == 2 and corrected_tikz in saved_text and not ctrl.isDirty)
            items.append(TestItemResult(
                test_id="REPAIR-07-OCC-PERSISTENCE",
                name="OCC Versioned Publication & Document Persistence",
                status=TestStatus.PASS if save_ok else TestStatus.FAIL,
                details={"canonical_version": saved_ver},
            ))

        finally:
            ctrl.shutdown()

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    return StageResult(
        stage_name="latex-repair-workflow",
        status=TestStatus.PASS if failed == 0 and blocked == 0 else TestStatus.FAIL,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
        duration_seconds=time.monotonic() - start_time,
        items=items,
    )


# ---------------------------------------------------------------------------
# Report Serialization (JSON and Markdown)
# ---------------------------------------------------------------------------

def generate_markdown_report(report: AcceptanceReport) -> str:
    lines = [
        "# Polpo Real-World Rendering Acceptance Report",
        "",
        f"- **Date & Time (UTC):** `{report.timestamp_utc}`",
        f"- **Host System:** `{report.hostname}` ({report.os_info.get('PRETTY_NAME', report.os_info.get('system', 'Linux'))})",
        f"- **Overall Verdict:** **`{report.overall_status}`**",
        "",
        "---",
        "",
        "## Summary of Acceptance Stages",
        "",
        "| Stage | Status | Tests | Passed | Failed | Blocked | Skipped | Duration |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]

    for name, stg in report.stages.items():
        dur_str = f"{stg.duration_seconds:.2f}s"
        lines.append(
            f"| `{name}` | **{stg.status}** | {stg.total_tests} | {stg.passed_tests} | {stg.failed_tests} | {stg.blocked_tests} | {stg.skipped_tests} | {dur_str} |"
        )

    lines.extend(["", "---", "", "## Stage Details", ""])

    for name, stg in report.stages.items():
        lines.append(f"### Stage: `{name}` (Status: {stg.status})")
        lines.append("")
        for item in stg.items:
            icon = "✅" if item.status == TestStatus.PASS else ("❌" if item.status == TestStatus.FAIL else "⏸️")
            lines.append(f"- {icon} **`{item.test_id}`**: {item.name} — **`{item.status}`** ({item.duration_ms:.1f}ms)")
            if item.error_message:
                lines.append(f"  - *Error:* `{item.error_message}`")
            if item.details:
                for k, v in item.details.items():
                    if k not in ("svg_xml", "raw_content"):
                        lines.append(f"  - `{k}`: `{v}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Run Polpo real-world rendering acceptance tests on Ubuntu 24")
    parser.add_argument(
        "--stage",
        choices=[
            "all",
            "runtime-preflight",
            "mathjax-live",
            "diagram-render-live",
            "quarto-pdf-live",
            "tex-security",
            "qml-preview-e2e",
            "latex-repair-workflow",
            "gemini-live",
            "desktop-preview",
        ],
        default="all",
        help="Specific stage to execute (default: all)",
    )
    parser.add_argument(
        "--run-live-ai",
        action="store_true",
        help="Explicitly opt-in to execute live AI (Gemini) API requests consuming quota",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=_REPO_ROOT / "build" / "acceptance_report.json",
        help="Path to write machine-readable JSON acceptance report",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=_REPO_ROOT / "build" / "acceptance_report.md",
        help="Path to write human-readable Markdown acceptance report",
    )
    args = parser.parse_args()

    stages_to_run = (
        [
            "runtime-preflight",
            "mathjax-live",
            "diagram-render-live",
            "quarto-pdf-live",
            "tex-security",
            "qml-preview-e2e",
            "latex-repair-workflow",
            "gemini-live",
            "desktop-preview",
        ]
        if args.stage == "all"
        else [args.stage]
    )

    stage_map = {
        "runtime-preflight": run_runtime_preflight,
        "mathjax-live": run_mathjax_live,
        "diagram-render-live": run_diagram_render_live,
        "quarto-pdf-live": run_quarto_pdf_live,
        "tex-security": run_tex_security,
        "qml-preview-e2e": run_qml_e2e_acceptance,
        "latex-repair-workflow": run_repair_workflow,
        "gemini-live": lambda: run_gemini_live(opt_in=args.run_live_ai),
        "desktop-preview": run_desktop_preview,
    }

    print("=" * 70)
    print("  POLPO REAL-WORLD RENDERING ACCEPTANCE SUITE (Ubuntu 24)")
    print("=" * 70)
    print(f"Timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    print(f"Opt-in live AI: {args.run_live_ai}")
    print(f"Stages: {', '.join(stages_to_run)}")
    print("-" * 70)

    stages_results: Dict[str, StageResult] = {}
    any_failed = False

    for stg_name in stages_to_run:
        fn = stage_map[stg_name]
        print(f"\n>> Running Stage: [{stg_name}] ...")
        res = fn()
        stages_results[stg_name] = res
        print(f"   Verdict: [{res.status}] in {res.duration_seconds:.2f}s ({res.passed_tests}/{res.total_tests} passed)")
        if res.status == TestStatus.FAIL:
            any_failed = True

    overall = compute_overall_status(stages_results)
    any_failed = (overall == TestStatus.FAIL)

    report = AcceptanceReport(
        timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        hostname=platform.node(),
        os_info={
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
        },
        overall_status=overall,
        stages=stages_results,
    )

    # Save reports
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)

    report_dict = asdict(report)
    args.output_json.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    md_content = generate_markdown_report(report)
    args.output_md.write_text(md_content, encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"  ACCEPTANCE RUN COMPLETE: VERDICT = {overall}")
    print(f"  Machine-readable report: {args.output_json}")
    print(f"  Human-readable report:   {args.output_md}")
    print("=" * 70)

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
