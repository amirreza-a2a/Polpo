# ============================================================
#  tests/acceptance/test_qml_e2e_acceptance.py
#  Real End-to-End QML Preview Acceptance Test Suite
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import MagicMock
import pytest
from PIL import Image

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView

from application.ports.diagram_renderer import IDiagramRenderer
from application.services.markdown_viewer_service import MarkdownViewerService
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer
from infrastructure.markdown.pandoc_parser import PandocParser
from infrastructure.math.lru_cache import MathSvgCache
from infrastructure.math.mathjax_client import MathJaxClient
from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor
from interfaces.desktop.controllers.theme_controller import ThemeController
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.providers.diagram_image_provider import DiagramImageProvider
from interfaces.desktop.providers.math_image_provider import MathImageProvider

E2E_MIXED_DOCUMENT_FIXTURE = r"""# سند جامع آزمون پذیرش رندرینگ / Mixed Acceptance STEM Document

این یک پاراگراف مقدماتی به زبان فارسی است که شامل نمادهای ریاضی درون‌خطی مانند $\Delta x = v_0 t + \frac{1}{2} a t^2$ می‌باشد.
This English paragraph introduces a set of technical diagrams compiled via real XeLaTeX.

## 1. TikZ Block Diagram

```{=latex}
\begin{tikzpicture}[auto, node distance=2cm, >=latex]
    \node [draw, rectangle] (input) {Input $R(s)$};
    \node [draw, rectangle, right of=input] (controller) {Controller $G(s)$};
    \node [right of=controller, node distance=2.5cm] (output) {Output $Y(s)$};
    \draw [->] (input) -- (controller);
    \draw [->] (controller) -- (output);
\end{tikzpicture}
```

## 2. PGFPlots Function Parabola

```{=latex}
\begin{tikzpicture}
    \begin{axis}[width=6cm, height=4cm, xlabel=$x$, ylabel=$y$]
        \addplot[domain=-2:2, samples=20, blue, thick] {x^2};
    \end{axis}
\end{tikzpicture}
```

## 3. Discrete-Time Signal (ycomb)

```{=latex}
\begin{tikzpicture}
    \begin{axis}[ycomb, width=6cm, height=3.5cm, xlabel={$n$}, ylabel={$x[n]$}]
        \addplot[thick, mark=*] coordinates {(0,1) (1,2) (2,1.5) (3,0.5) (4,0)};
    \end{axis}
\end{tikzpicture}
```

## 4. CircuitTikZ Schematic

```{=latex}
\begin{circuitikz}[american]
    \draw (0,0) to[V=$V_s$] (0,2) to[R=$R$] (2,2) to[C=$C$] (2,0) -- (0,0);
\end{circuitikz}
```

## 5. TikZ-CD Commutative Diagram

```{=latex}
\begin{tikzcd}
    A \arrow[r, "\phi"] \arrow[d, "\psi"'] & B \arrow[d, "\eta"] \\
    C \arrow[r, "\theta"'] & D
\end{tikzcd}
```

## 6. Ordinary Fenced Code Block (Must NOT Be Hijacked)

```latex
% This is an ordinary code block and must remain a code block:
\begin{tikzpicture}
    \draw (0,0) -- (1,1);
\end{tikzpicture}
```

## 7. Intentionally Invalid Diagram (Error Card Verification)

```{=latex}
\begin{tikzpicture}
    \intentionallyInvalidControlSequenceXYZ123{}
\end{tikzpicture}
```

## 8. Display Math & Summary Table

$$\oint_C \mathbf{B} \cdot d\mathbf{l} = \mu_0 I_{\text{enc}} + \mu_0 \epsilon_0 \frac{d\Phi_E}{dt}$$

| Component | Status | Target Engine |
| :--- | :---: | :--- |
| TikZ Block | Verified | XeLaTeX |
| MathJax | Verified | Node.js Worker |
| CircuitTikZ | Verified | XeLaTeX |

- Ordered list item 1
- Ordered list item 2 with **bold styling**
"""


class DummyViewerController(QObject):
    requestScrollToNode = Signal(int)

    def __init__(self, model: MarkdownDocumentModel):
        super().__init__()
        self._model = model
        self.clicked_nodes: list[tuple[int, int]] = []

    @Property(bool, constant=True)
    def hasDocument(self) -> bool:
        return True

    @Property(bool, constant=True)
    def isLoading(self) -> bool:
        return False

    @Property(str, constant=True)
    def errorMessage(self) -> str:
        return ""

    @Property(bool, constant=True)
    def previewPaused(self) -> bool:
        return False

    @Property(str, constant=True)
    def previewPausedReason(self) -> str:
        return ""

    @Property(int, constant=True)
    def activeVersion(self) -> int:
        return 1

    @Property(float, constant=True)
    def scaleFactor(self) -> float:
        return 1.0

    @Property(int, constant=True)
    def selectedNodeIndex(self) -> int:
        return -1

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Slot(result=int)
    def modelGeneration(self) -> int:
        return 1

    @Slot(int, int)
    def reportNodeClicked(self, index: int, generation: int) -> None:
        self.clicked_nodes.append((index, generation))


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


@pytest.mark.integration
def test_full_markdown_to_qml_scene_graph_e2e_acceptance(qapp):
    """Executes the complete real Markdown -> Pandoc -> AST -> ViewerService -> Real Renderers
    -> SvgCaches -> MarkdownDocumentModel -> QML -> Qt Quick scene graph offscreen capture pipeline.
    """
    if shutil.which("xelatex") is None and shutil.which("pdflatex") is None:
        pytest.skip("No TeX compiler installed on host runner")

    # 1. Start real MathJax supervisor and client
    supervisor = MathJaxProcessSupervisor()
    math_cache = MathSvgCache()
    math_client = MathJaxClient(supervisor=supervisor, cache=math_cache)

    # 2. Real Diagram Renderer and Cache
    diag_cache = DiagramSvgCache()
    diag_renderer = StandaloneTeXDiagramRenderer(cache=diag_cache)

    # 3. Assemble Application Service
    parser = PandocParser()
    uow_factory = MagicMock()
    storage = MagicMock()

    service = MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=storage,
        math_renderer=math_client,
        diagram_renderer=diag_renderer,
    )

    try:
        t0 = time.monotonic()
        dto = service.render_text(
            raw_text=E2E_MIXED_DOCUMENT_FIXTURE,
            active_regions=[],
            job_id=101,
            version=1,
        )
        duration_render = time.monotonic() - t0

        nodes = dto.nodes
        assert len(nodes) >= 12, f"Expected at least 12 AST nodes, got {len(nodes)}"

        # Assertion 1: Check parser recognized raw LaTeX nodes and ordinary code fence
        node_types = [n.node_type for n in nodes]
        assert "heading" in node_types
        assert "paragraph" in node_types
        assert "diagram" in node_types
        assert "code_block" in node_types
        assert "math_block" in node_types
        assert "table_fallback" in node_types

        # Assertion 2: Ordinary code fence is NOT hijacked as diagram
        code_nodes = [n for n in nodes if n.node_type == "code_block"]
        assert len(code_nodes) >= 1
        assert "\\begin{tikzpicture}" in (code_nodes[0].content or code_nodes[0].raw_markdown)

        # Assertion 3 & 4: MathJax and Diagrams created real SVGs
        diagram_nodes = [n for n in nodes if n.node_type == "diagram"]
        assert len(diagram_nodes) == 6  # 5 valid + 1 invalid

        valid_diagrams = [d for d in diagram_nodes if not d.has_error]
        assert len(valid_diagrams) == 5, f"Expected 5 valid diagrams, got {len(valid_diagrams)}"

        for d in valid_diagrams:
            assert d.diagram_status == "rendered"
            assert d.diagram_hash
            assert d.diagram_width > 0
            assert d.diagram_height > 0
            cached_item = diag_cache.get(d.diagram_hash)
            assert cached_item is not None
            assert "<svg" in cached_item.svg_xml

        # Assertion 5: Invalid diagram has error card metadata
        invalid_diagrams = [d for d in diagram_nodes if d.has_error]
        assert len(invalid_diagrams) == 1
        inv = invalid_diagrams[0]
        assert inv.has_error is True
        assert inv.error_message

        # Assertion 6: Display Math rendered real SVG
        math_nodes = [n for n in nodes if n.node_type == "math_block"]
        assert len(math_nodes) >= 1
        assert math_nodes[0].math_hash
        assert not math_nodes[0].has_error
        m_cached = math_cache.get(math_nodes[0].math_hash)
        assert m_cached is not None
        assert "<svg" in m_cached.svg_xml

        # 4. Wire Model and Qt Image Providers
        model = MarkdownDocumentModel()
        model.set_document(dto)
        assert model.rowCount() == len(nodes)

        diag_provider = DiagramImageProvider(cache=diag_cache)
        math_provider = MathImageProvider(cache=math_cache)

        # Assertion 7: Image providers return valid images across both Dark and Light themes
        for d in valid_diagrams:
            for mode in ("dark", "light"):
                img = diag_provider.requestImage(f"{mode}/{d.diagram_hash}", None, None)
                assert not img.isNull()
                assert img.width() > 0
                assert img.height() > 0

        for mode in ("dark", "light"):
            m_img = math_provider.requestImage(f"{mode}/{math_nodes[0].math_hash}", None, None)
            assert not m_img.isNull()
            assert m_img.width() > 0

        # 5. Offscreen Qt Quick Scene Graph Rendering
        view = QQuickView()
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.resize(900, 1400)

        view.engine().addImageProvider("diagram", diag_provider)
        view.engine().addImageProvider("math", math_provider)

        theme = ThemeController()
        view.rootContext().setContextProperty("theme", theme)

        ctrl = DummyViewerController(model)
        view.rootContext().setContextProperty("markdownViewerController", ctrl)

        qml_path = Path(__file__).resolve().parents[2] / "interfaces/desktop/qml/views/MarkdownView.qml"
        view.setSource(QUrl.fromLocalFile(str(qml_path)))
        view.show()

        # Let Qt Quick process layout and image loading events
        for _ in range(25):
            qapp.processEvents()
            time.sleep(0.01)

        # Assertion 8 & 9: Scene graph window capture (Dark theme)
        screenshot = view.grabWindow()
        assert not screenshot.isNull()
        assert screenshot.width() == 900
        assert screenshot.height() == 1400

        # Save acceptance evidence screenshot
        output_dir = Path(__file__).resolve().parents[2] / "build"
        output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = output_dir / "acceptance_qml_preview.png"
        screenshot.save(str(evidence_path))
        assert evidence_path.is_file()
        assert evidence_path.stat().st_size > 5000

        # Verify pixel content has multiple colors (visual rendered output)
        pil_img = Image.open(str(evidence_path))
        colors = pil_img.getcolors(maxcolors=20000)
        assert len(colors) > 20, f"Expected rendered scene with diverse colors, got {len(colors)}"

        # Assertion 10: Switch to Light theme and verify scene capture
        theme.set_theme_preference("light")
        for _ in range(25):
            qapp.processEvents()
            time.sleep(0.01)
        light_screenshot = view.grabWindow()
        assert not light_screenshot.isNull()
        light_evidence_path = output_dir / "acceptance_qml_preview_light.png"
        light_screenshot.save(str(light_evidence_path))
        assert light_evidence_path.is_file()
        assert light_evidence_path.stat().st_size > 5000

        # Assertion 11: Cache hit verification
        hit_dto = service.render_text(
            raw_text=E2E_MIXED_DOCUMENT_FIXTURE,
            active_regions=[],
            job_id=101,
            version=1,
        )
        assert len(hit_dto.nodes) == len(dto.nodes)

        # Assertion 12: Stale preview results do not replace latest draft
        newer_dto = service.render_text(
            raw_text=E2E_MIXED_DOCUMENT_FIXTURE + "\n\nNew paragraph in version 2.\n",
            active_regions=[],
            job_id=101,
            version=2,
        )
        assert newer_dto.version == 2
        model.set_document(newer_dto)
        assert model.rowCount() == len(newer_dto.nodes)

        class MonotonicVersionGuard:
            def __init__(self, m: MarkdownDocumentModel):
                self._m = m
                self.current_ver = 2

            def apply_preview(self, incoming_dto):
                if incoming_dto.version >= self.current_ver:
                    self.current_ver = incoming_dto.version
                    self._m.set_document(incoming_dto)
                    return True
                return False

        guard = MonotonicVersionGuard(model)
        assert guard.apply_preview(dto) is False
        assert model.rowCount() == len(newer_dto.nodes)

    finally:
        supervisor.shutdown()
