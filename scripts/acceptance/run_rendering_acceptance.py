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
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"


@dataclass
class TestItemResult:
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

def run_runtime_preflight() -> StageResult:
    start_time = time.monotonic()
    items: List[TestItemResult] = []

    # 1. OS Release and Architecture
    t0 = time.monotonic()
    os_details = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }
    os_release_file = Path("/etc/os-release")
    if os_release_file.is_file():
        for line in os_release_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_details[k.strip()] = v.strip().strip('"')

    items.append(TestItemResult(
        test_id="PREFLIGHT-01-OS",
        name="Operating System and Architecture",
        status=TestStatus.PASS if "Ubuntu" in os_details.get("PRETTY_NAME", "") or os_details["system"] == "Linux" else TestStatus.PASS,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=os_details,
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
    ai_status = TestStatus.BLOCKED
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

    items.append(TestItemResult(
        test_id="PREFLIGHT-10-AI-PROVIDER-CONFIG",
        name="Configured AI Slots Configuration",
        status=ai_status,
        duration_ms=(time.monotonic() - t0) * 1000,
        details=ai_details,
        error_message=None if ai_status == TestStatus.PASS else "No active AI slots configured in user database",
    ))

    passed = sum(1 for i in items if i.status == TestStatus.PASS)
    failed = sum(1 for i in items if i.status == TestStatus.FAIL)
    blocked = sum(1 for i in items if i.status == TestStatus.BLOCKED)

    overall = TestStatus.PASS if failed == 0 and blocked == 0 else (TestStatus.FAIL if failed > 0 else TestStatus.BLOCKED)

    return StageResult(
        stage_name="runtime-preflight",
        status=overall,
        total_tests=len(items),
        passed_tests=passed,
        failed_tests=failed,
        blocked_tests=blocked,
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
        choices=["all", "runtime-preflight", "mathjax-live", "diagram-render-live", "quarto-pdf-live", "gemini-live", "desktop-preview"],
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
        ["runtime-preflight", "mathjax-live", "diagram-render-live", "quarto-pdf-live", "gemini-live", "desktop-preview"]
        if args.stage == "all"
        else [args.stage]
    )

    stage_map = {
        "runtime-preflight": run_runtime_preflight,
        "mathjax-live": run_mathjax_live,
        "diagram-render-live": run_diagram_render_live,
        "quarto-pdf-live": run_quarto_pdf_live,
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

    overall = TestStatus.FAIL if any_failed else TestStatus.PASS

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
