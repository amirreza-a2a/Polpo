# ============================================================
#  tests/unit/test_latex_repair_qml.py
#  QML Unit tests for LaTeX Repair Dialog & Presentation Components
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QQmlApplicationEngine,
    QQmlComponent,
    QUrl,
)
from interfaces.desktop.controllers.theme_controller import ThemeController


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_latex_repair_dialog_qml_loads(qapp):
    engine = QQmlApplicationEngine()
    theme = ThemeController()
    engine.rootContext().setContextProperty("theme", theme)
    engine.rootContext().setContextProperty("markdownEditorController", MagicMock())

    qml_path = Path(__file__).resolve().parents[2] / "interfaces/desktop/qml/dialogs/LaTeXRepairDialog.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
    assert component.status() == QQmlComponent.Status.Ready, f"Errors: {[e.toString() for e in component.errors()]}"

    dialog = component.create()
    assert dialog is not None
    assert dialog.objectName() == "latexRepairDialog"

    # Set dummy proposal
    proposal = {
        "requestId": "rep-test",
        "jobId": 1,
        "canonicalVersion": 1,
        "originalSource": "\\frac{1}{0",
        "repairedSource": "\\frac{1}{0}",
        "changeSummary": "Fixed missing brace",
        "preservedInvariants": ["math_valid"],
        "validationPassed": True,
        "validationError": "",
        "renderedPreviewXml": "<svg width='10' height='10'></svg>",
        "modelName": "gemini-2.5-flash",
    }
    dialog.setProperty("proposal", proposal)
    prop = dialog.property("proposal")
    val = prop.toVariant() if hasattr(prop, "toVariant") else prop
    assert val["repairedSource"] == "\\frac{1}{0}"


def test_math_error_card_qml_loads(qapp):
    engine = QQmlApplicationEngine()
    theme = ThemeController()
    engine.rootContext().setContextProperty("theme", theme)
    mock_editor = MagicMock()
    mock_editor.isRepairing = False
    engine.rootContext().setContextProperty("markdownEditorController", mock_editor)

    qml_path = Path(__file__).resolve().parents[2] / "interfaces/desktop/qml/components/MathErrorCard.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
    assert component.status() == QQmlComponent.Status.Ready, f"Errors: {[e.toString() for e in component.errors()]}"

    card = component.create()
    assert card is not None
    assert card.objectName() == "mathErrorCard"
