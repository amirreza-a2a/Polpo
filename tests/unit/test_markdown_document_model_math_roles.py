"""Unit tests for MarkdownDocumentModel math error roles and delegates."""

import pytest
from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
)
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import QGuiApplication, Qt


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_math_roles_definition():
    """Verify role constants have exact expected values."""
    assert MarkdownDocumentModel.MathHasErrorRole == Qt.ItemDataRole.UserRole + 28
    assert MarkdownDocumentModel.MathErrorCategoryRole == Qt.ItemDataRole.UserRole + 29
    assert MarkdownDocumentModel.MathErrorMessageRole == Qt.ItemDataRole.UserRole + 30


def test_math_role_names():
    """Verify roleNames() exposes byte strings for QML bindings."""
    model = MarkdownDocumentModel()
    roles = model.roleNames()

    assert roles.get(MarkdownDocumentModel.MathHasErrorRole) == b"mathHasError"
    assert roles.get(MarkdownDocumentModel.MathErrorCategoryRole) == b"mathErrorCategory"
    assert roles.get(MarkdownDocumentModel.MathErrorMessageRole) == b"mathErrorMessage"


def test_math_error_node_data(qapp):
    """Verify data() returns correct error attributes for a node with math failure."""
    model = MarkdownDocumentModel()
    error_node = MarkdownNodeDTO(
        node_id="math_err_1",
        node_type="math_block",
        content="\\frac{1}{0}",
        raw_markdown="$$\\frac{1}{0}$$",
        math_tex="\\frac{1}{0}",
        math_hash="hash_err_123",
        has_error=True,
        error_category="timeout",
        error_message="MathJax rendering timed out after 3.0s",
    )
    doc = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(error_node,),
        region_to_occurrences={},
    )
    model.set_document(doc)

    idx = model.index(0, 0)
    assert model.data(idx, MarkdownDocumentModel.MathHasErrorRole) is True
    assert model.data(idx, MarkdownDocumentModel.MathErrorCategoryRole) == "timeout"
    assert model.data(idx, MarkdownDocumentModel.MathErrorMessageRole) == "MathJax rendering timed out after 3.0s"
    assert model.data(idx, MarkdownDocumentModel.MathTexRole) == "\\frac{1}{0}"
    assert model.data(idx, MarkdownDocumentModel.MathHashRole) == "hash_err_123"


def test_successful_node_data_defaults(qapp):
    """Verify data() returns defaults for a node without error."""
    model = MarkdownDocumentModel()
    success_node = MarkdownNodeDTO(
        node_id="math_succ_1",
        node_type="math_block",
        content="x^2 + y^2 = z^2",
        raw_markdown="$$x^2 + y^2 = z^2$$",
        math_tex="x^2 + y^2 = z^2",
        math_hash="hash_succ_456",
        has_error=False,
        error_category="",
        error_message="",
    )
    doc = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(success_node,),
        region_to_occurrences={},
    )
    model.set_document(doc)

    idx = model.index(0, 0)
    assert model.data(idx, MarkdownDocumentModel.MathHasErrorRole) is False
    assert model.data(idx, MarkdownDocumentModel.MathErrorCategoryRole) == ""
    assert model.data(idx, MarkdownDocumentModel.MathErrorMessageRole) == ""


def test_backward_compatibility_unrelated_nodes(qapp):
    """Verify existing roles and paragraph nodes remain unaffected."""
    model = MarkdownDocumentModel()
    para_node = MarkdownNodeDTO(
        node_id="p_1",
        node_type="paragraph",
        content="Hello world",
        raw_markdown="Hello world",
    )
    doc = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(para_node,),
        region_to_occurrences={},
    )
    model.set_document(doc)

    idx = model.index(0, 0)
    assert model.data(idx, MarkdownDocumentModel.NodeIdRole) == "p_1"
    assert model.data(idx, MarkdownDocumentModel.NodeTypeRole) == "paragraph"
    assert model.data(idx, MarkdownDocumentModel.ContentRole) == "Hello world"
    assert model.data(idx, MarkdownDocumentModel.MathHasErrorRole) is False
    assert model.data(idx, MarkdownDocumentModel.MathErrorCategoryRole) == ""
    assert model.data(idx, MarkdownDocumentModel.MathErrorMessageRole) == ""


def test_inline_segment_math_error_attributes_in_item(qapp):
    """Verify inline segments preserve math error attributes for QML flow."""
    model = MarkdownDocumentModel()
    inline_err_seg = InlineSegmentDTO(
        segment_type="math",
        text_html='<span class="math-error">$x$</span>',
        math_tex="x",
        math_hash="hash_x",
        has_error=True,
        error_category="syntax",
        error_message="Unexpected token",
    )
    node = MarkdownNodeDTO(
        node_id="p_math",
        node_type="paragraph",
        content="Formula",
        segments=(inline_err_seg,),
    )
    doc = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(node,),
        region_to_occurrences={},
    )
    model.set_document(doc)

    idx = model.index(0, 0)
    segments = model.data(idx, MarkdownDocumentModel.SegmentsRole)
    assert len(segments) == 1
    seg = segments[0]
    assert seg["segmentType"] == "math"
    assert seg["mathTex"] == "x"
    assert seg["mathHash"] == "hash_x"
    assert seg["hasError"] is True
    assert seg["errorCategory"] == "syntax"
    assert seg["errorMessage"] == "Unexpected token"


def test_reconcile_document_updates_math_error_in_place(qapp):
    """Verify reconcile_document updates math error state in-place with dataChanged signal."""
    model = MarkdownDocumentModel()
    math_node_v1 = MarkdownNodeDTO(
        node_id="math_1",
        node_type="math_block",
        content="\\frac{1}{2}",
        raw_markdown="$$\\frac{1}{2}$$",
        math_tex="\\frac{1}{2}",
        math_hash="hash_12",
        has_error=False,
    )
    doc_v1 = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(math_node_v1,),
        region_to_occurrences={},
    )
    model.set_document(doc_v1)
    assert model.data(model.index(0, 0), MarkdownDocumentModel.MathHasErrorRole) is False

    changes = []
    model.dataChanged.connect(lambda tl, br, roles: changes.append((tl.row(), roles)))

    math_node_v2 = MarkdownNodeDTO(
        node_id="math_1",
        node_type="math_block",
        content="\\frac{1}{2}",
        raw_markdown="$$\\frac{1}{2}$$",
        math_tex="\\frac{1}{2}",
        math_hash="hash_12",
        has_error=True,
        error_category="timeout",
        error_message="Rendering timed out",
    )
    doc_v2 = MarkdownDocumentDTO(
        job_id=1,
        version=2,
        nodes=(math_node_v2,),
        region_to_occurrences={},
    )
    model.reconcile_document(doc_v2)

    assert len(changes) == 1
    row, roles = changes[0]
    assert row == 0
    assert MarkdownDocumentModel.MathHasErrorRole in roles
    assert MarkdownDocumentModel.MathErrorCategoryRole in roles
    assert MarkdownDocumentModel.MathErrorMessageRole in roles
    assert model.data(model.index(0, 0), MarkdownDocumentModel.MathHasErrorRole) is True
    assert model.data(model.index(0, 0), MarkdownDocumentModel.MathErrorCategoryRole) == "timeout"
    assert model.data(model.index(0, 0), MarkdownDocumentModel.MathErrorMessageRole) == "Rendering timed out"


def test_math_error_card_qml_loads_and_safe_invocation(qapp):
    """Verify MathErrorCard.qml loads cleanly in QQmlEngine and handles null-safe clipboard calls."""
    from pathlib import Path
    from interfaces.desktop.qt_compat import QQmlApplicationEngine, QQmlComponent, QUrl, QObject, Slot

    engine = QQmlApplicationEngine()
    qml_file = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
        / "MathErrorCard.qml"
    )
    assert qml_file.exists()

    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file)))
    assert not component.isError(), f"QML load error: {component.errors()}"

    obj = component.create()
    assert obj is not None

    obj.setProperty("mathTex", "\\frac{a}{b}")
    obj.setProperty("category", "timeout")
    obj.setProperty("errorMessage", "Computation timed out")

    assert obj.property("categoryLabel") == "Timeout"

    # Verify defensive null-safe copyTex invocation when controller is null
    obj.copyTex()

    # Verify copyTex with mock controller containing copyToClipboard slot
    class MockClipboardController(QObject):
        def __init__(self):
            super().__init__()
            self.copied_text = None

        @Slot(str)
        def copyToClipboard(self, text: str):
            self.copied_text = text

    mock_ctrl = MockClipboardController()
    obj.setProperty("controller", mock_ctrl)
    obj.copyTex()
    assert mock_ctrl.copied_text == "\\frac{a}{b}"


def test_markdown_node_delegate_and_inline_flow_qml_syntax(qapp):
    """Verify MarkdownNodeDelegate.qml and MarkdownInlineFlow.qml compile cleanly without syntax errors."""
    from pathlib import Path
    from interfaces.desktop.qt_compat import QQmlApplicationEngine, QQmlComponent, QUrl

    engine = QQmlApplicationEngine()
    components_dir = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "components"
    )

    for qml_name in ["MarkdownInlineFlow.qml", "MarkdownNodeDelegate.qml"]:
        qml_path = components_dir / qml_name
        assert qml_path.exists()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
        assert not component.isError(), f"Error in {qml_name}: {component.errors()}"
