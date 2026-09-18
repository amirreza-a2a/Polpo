# ============================================================
#  tests/unit/test_review_workspace_editor_integration.py
#  Integration tests for Review Workspace with Native Markdown Editor
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.artifact import ArtifactType
from interfaces.desktop.app import wire_review_workspace_sync
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QObject
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from application.services.apply_review_service import ApplyReviewService
from application.services.document_viewer_service import DocumentViewerService
from application.services.markdown_editor_service import MarkdownEditorService
from application.services.markdown_viewer_service import MarkdownViewerService


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def workspace_env(tmp_path):
    db_path = tmp_path / "workspace_test.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))
    doc_proc = PyMuPDFDocumentProcessor()
    md_parser = MarkdownItParser()

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        uow.commit()

    apply_service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    doc_viewer_service = DocumentViewerService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    md_viewer_service = MarkdownViewerService(parser=md_parser, uow_factory=uow_factory, storage=storage)
    md_editor_service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    doc_ctrl = DocumentViewerController(viewer_service=doc_viewer_service, apply_review_service=apply_service)
    md_viewer_ctrl = MarkdownViewerController(viewer_service=md_viewer_service)
    md_editor_ctrl = MarkdownEditorController(editor_service=md_editor_service)

    wire_review_workspace_sync(doc_ctrl, md_viewer_ctrl, md_editor_ctrl)

    return {
        "uow_factory": uow_factory,
        "storage": storage,
        "prompt_id": prompt_id,
        "doc_ctrl": doc_ctrl,
        "md_viewer_ctrl": md_viewer_ctrl,
        "md_editor_ctrl": md_editor_ctrl,
    }


def test_editor_save_triggers_viewer_reload(workspace_env):
    md_viewer_ctrl = workspace_env["md_viewer_ctrl"]
    md_editor_ctrl = workspace_env["md_editor_ctrl"]

    # Mock loadDocument on viewer controller
    md_viewer_ctrl.loadDocument = MagicMock()

    # Set up active job on editor
    md_editor_ctrl._active_job_id = 99
    md_editor_ctrl.saved.emit(2)

    md_viewer_ctrl.loadDocument.assert_called_once_with(99)


def test_viewer_version_change_notifies_editor(workspace_env):
    md_viewer_ctrl = workspace_env["md_viewer_ctrl"]
    md_editor_ctrl = workspace_env["md_editor_ctrl"]

    md_editor_ctrl.notifyCanonicalDocumentAdvance = MagicMock()

    # Simulate viewer setting active version
    md_viewer_ctrl._active_version = 4
    md_viewer_ctrl.activeVersionChanged.emit()

    md_editor_ctrl.notifyCanonicalDocumentAdvance.assert_called_once_with(4)


def _load_workspace_view(workspace_env):
    """Helper to instantiate ReviewWorkspaceView with injected controller context properties."""
    doc_ctrl = workspace_env["doc_ctrl"]
    md_viewer_ctrl = workspace_env["md_viewer_ctrl"]
    md_editor_ctrl = workspace_env["md_editor_ctrl"]

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("documentViewerController", doc_ctrl)
    ctx.setContextProperty("markdownViewerController", md_viewer_ctrl)
    ctx.setContextProperty("markdownEditorController", md_editor_ctrl)

    qml_file = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "views" / "ReviewWorkspaceView.qml"
    engine.load(str(qml_file))

    root_objs = engine.rootObjects()
    assert len(root_objs) > 0, "Failed to load ReviewWorkspaceView.qml"
    workspace = root_objs[-1]
    QGuiApplication.processEvents()
    return engine, workspace


def test_qml_review_workspace_instantiation(qapp, workspace_env):
    engine, workspace = _load_workspace_view(workspace_env)

    # Invariant: documentViewerView, markdownPane, markdownView, and markdownEditorPane exist
    pdf_pane = workspace.findChild(QObject, "documentViewerView")
    assert pdf_pane is not None

    md_pane = workspace.findChild(QObject, "markdownPane")
    assert md_pane is not None

    md_view = workspace.findChild(QObject, "markdownView")
    assert md_view is not None

    md_editor = workspace.findChild(QObject, "markdownEditorPane")
    assert md_editor is not None

    # Check tab buttons
    preview_btn = workspace.findChild(QObject, "previewTabButton")
    assert preview_btn is not None

    editor_btn = workspace.findChild(QObject, "editorTabButton")
    assert editor_btn is not None

    # Tab toggling
    assert md_pane.property("currentTab") == 0
    editor_btn.clicked.emit()
    assert md_pane.property("currentTab") == 1
    preview_btn.clicked.emit()
    assert md_pane.property("currentTab") == 0


def test_qml_splitview_tab0_preview_only(qapp, workspace_env):
    """T-F3-QML-01: In Tab 0, verify markdownView.width > 0 and markdownEditorPane.width == 0."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_pane = workspace.findChild(QObject, "markdownPane")
    md_view = workspace.findChild(QObject, "markdownView")
    md_editor = workspace.findChild(QObject, "markdownEditorPane")

    assert md_pane.property("currentTab") == 0
    assert md_view.property("visible") is True
    assert md_editor.property("visible") is False
    assert md_view.property("width") > 0
    assert md_editor.property("width") == 0


def test_qml_splitview_tab1_editor_only(qapp, workspace_env):
    """T-F3-QML-02: In Tab 1, verify markdownEditorPane.width > 0 and markdownView.width == 0."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_pane = workspace.findChild(QObject, "markdownPane")
    md_view = workspace.findChild(QObject, "markdownView")
    md_editor = workspace.findChild(QObject, "markdownEditorPane")
    editor_btn = workspace.findChild(QObject, "editorTabButton")

    editor_btn.clicked.emit()
    QGuiApplication.processEvents()

    assert md_pane.property("currentTab") == 1
    assert md_editor.property("visible") is True
    assert md_view.property("visible") is False
    assert md_editor.property("width") > 0
    assert md_view.property("width") == 0


def test_qml_splitview_tab2_dual_pane_and_handle(qapp, workspace_env):
    """T-F3-QML-03: In Tab 2, verify markdownEditorPane.width > 0, markdownView.width > 0, and splitter handle is visible."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_pane = workspace.findChild(QObject, "markdownPane")
    md_view = workspace.findChild(QObject, "markdownView")
    md_editor = workspace.findChild(QObject, "markdownEditorPane")
    split_btn = workspace.findChild(QObject, "splitTabButton")
    preview_btn = workspace.findChild(QObject, "previewTabButton")

    assert split_btn is not None, "splitTabButton must exist"
    split_btn.clicked.emit()
    QGuiApplication.processEvents()

    assert md_pane.property("currentTab") == 2
    assert md_editor.property("visible") is True
    assert md_view.property("visible") is True
    assert md_editor.property("width") > 0
    assert md_view.property("width") > 0

    handle = workspace.findChild(QObject, "rightSplitHandle")
    assert handle is not None, "rightSplitHandle must exist"
    assert handle.property("visible") is True

    # Switching back to Tab 0 hides the handle
    preview_btn.clicked.emit()
    QGuiApplication.processEvents()
    assert handle.property("visible") is False


def test_qml_dual_pane_tab_switching_preserves_editor_state(qapp, workspace_env):
    """T-F3-QML-04: Type text in Editor (Tab 1), switch to Tab 2, switch to Tab 0, switch back to Tab 1; verify text, cursor, and undo history are preserved."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_pane = workspace.findChild(QObject, "markdownPane")
    editor_btn = workspace.findChild(QObject, "editorTabButton")
    split_btn = workspace.findChild(QObject, "splitTabButton")
    preview_btn = workspace.findChild(QObject, "previewTabButton")

    # Switch to Editor tab
    editor_btn.clicked.emit()
    QGuiApplication.processEvents()

    source_text_area = workspace.findChild(QObject, "markdownSourceTextArea")
    assert source_text_area is not None

    # Insert text to establish undo history
    source_text_area.insert(0, "# Edited Document Line 1\nLine 2 content")
    QGuiApplication.processEvents()
    source_text_area.setProperty("cursorPosition", 8)
    QGuiApplication.processEvents()

    assert source_text_area.property("text") == "# Edited Document Line 1\nLine 2 content"
    assert source_text_area.property("cursorPosition") == 8
    assert source_text_area.property("canUndo") is True

    # Switch to Dual Pane (Tab 2)
    assert split_btn is not None
    split_btn.clicked.emit()
    QGuiApplication.processEvents()
    assert md_pane.property("currentTab") == 2

    # Switch to Preview Only (Tab 0)
    preview_btn.clicked.emit()
    QGuiApplication.processEvents()
    assert md_pane.property("currentTab") == 0

    # Switch back to Editor Only (Tab 1)
    editor_btn.clicked.emit()
    QGuiApplication.processEvents()
    assert md_pane.property("currentTab") == 1

    # Invariant: text, cursor, and undo history are preserved
    assert source_text_area.property("text") == "# Edited Document Line 1\nLine 2 content"
    assert source_text_area.property("cursorPosition") == 8
    assert source_text_area.property("canUndo") is True


def test_qml_dual_pane_preview_error_banner(qapp, workspace_env):
    """T-F3-QML-05: Set hasPreviewError = True on controller; verify previewErrorBanner becomes visible with expected error text."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_viewer_ctrl = workspace_env["md_viewer_ctrl"]

    banner = workspace.findChild(QObject, "previewErrorBanner")
    assert banner is not None, "previewErrorBanner must exist"
    assert banner.property("visible") is False

    # Simulate preview error
    md_viewer_ctrl._has_preview_error = True
    md_viewer_ctrl._preview_error_message = "Syntax error in math block"
    md_viewer_ctrl.hasPreviewErrorChanged.emit()
    md_viewer_ctrl.previewErrorChanged.emit()
    QGuiApplication.processEvents()

    assert banner.property("visible") is True
    error_text = workspace.findChild(QObject, "previewErrorText")
    assert error_text is not None
    assert "Syntax error in math block" in error_text.property("text")

    # Clear preview error
    md_viewer_ctrl._has_preview_error = False
    md_viewer_ctrl._preview_error_message = ""
    md_viewer_ctrl.hasPreviewErrorChanged.emit()
    md_viewer_ctrl.previewErrorChanged.emit()
    QGuiApplication.processEvents()

    assert banner.property("visible") is False


def test_qml_dual_pane_responsive_resizing(qapp, workspace_env):
    """T-F3-QML-06: In Tab 2, resize window width from 600 to 1000; verify both editor and preview expand responsively."""
    engine, workspace = _load_workspace_view(workspace_env)
    md_view = workspace.findChild(QObject, "markdownView")
    md_editor = workspace.findChild(QObject, "markdownEditorPane")
    split_btn = workspace.findChild(QObject, "splitTabButton")

    assert split_btn is not None
    split_btn.clicked.emit()
    workspace.setProperty("width", 600)
    QGuiApplication.processEvents()

    w_editor_600 = md_editor.property("width")
    w_view_600 = md_view.property("width")
    assert w_editor_600 > 0
    assert w_view_600 > 0

    workspace.setProperty("width", 1000)
    QGuiApplication.processEvents()

    w_editor_1000 = md_editor.property("width")
    w_view_1000 = md_view.property("width")
    assert w_editor_1000 > w_editor_600, f"Editor width {w_editor_1000} should be > {w_editor_600}"
    assert w_view_1000 > w_view_600, f"View width {w_view_1000} should be > {w_view_600}"


def test_qml_editor_pane_save_activation_and_shortcut(qapp, workspace_env):
    """
    Verifies:
    1. MarkdownEditorController.setSourceText() is invokable from QML without TypeError.
    2. Editing TextArea updates controller.isDirty to True.
    3. Save and Discard buttons become enabled.
    4. Discard button clears dirty state and disables Save button.
    5. Editing again and clicking Save triggers commit and disables Save upon completion.
    6. No QML Shortcut warning or TypeError occurs during loading/editing.
    """
    import time
    md_editor_ctrl = workspace_env["md_editor_ctrl"]
    uow_factory = workspace_env["uow_factory"]
    storage = workspace_env["storage"]
    prompt_id = workspace_env["prompt_id"]

    out_h = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", b"# Initial Content\n")
    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="/tmp/doc.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
                output_path=out_h.uri,
                output_artifact_version_watermark=1,
            )
        )
        job_id = job.id
        uow.commit()

    md_editor_ctrl.load_source_sync(job_id)

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownEditorController", md_editor_ctrl)

    qml_file = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "components" / "MarkdownEditorPane.qml"
    engine.load(str(qml_file))

    root_objs = engine.rootObjects()
    assert len(root_objs) > 0
    editor_root = root_objs[-1]

    text_area = editor_root.findChild(QObject, "markdownSourceTextArea")
    assert text_area is not None

    save_btn = editor_root.findChild(QObject, "editorSaveButton")
    assert save_btn is not None

    discard_btn = editor_root.findChild(QObject, "editorDiscardButton")
    assert discard_btn is not None

    # Initially buffer is clean -> Save and Discard disabled
    assert md_editor_ctrl.isDirty is False
    assert save_btn.property("enabled") is False
    assert discard_btn.property("enabled") is False

    # Simulate typing into TextArea
    text_area.setProperty("text", "# User Typed Content In QML")

    # Invariant: isDirty must be True, Save & Discard enabled
    assert md_editor_ctrl.isDirty is True
    assert md_editor_ctrl.sourceText == "# User Typed Content In QML"
    assert save_btn.property("enabled") is True
    assert discard_btn.property("enabled") is True

    # Test Discard action from QML
    discard_btn.clicked.emit()
    assert md_editor_ctrl.isDirty is False
    assert save_btn.property("enabled") is False
    assert discard_btn.property("enabled") is False

    # Modify again
    text_area.setProperty("text", "# Second Edit To Save")
    assert md_editor_ctrl.isDirty is True
    assert save_btn.property("enabled") is True

    # Save action via button
    save_btn.clicked.emit()
    for _ in range(50):
        QGuiApplication.processEvents()
        if not md_editor_ctrl.isSaving and not md_editor_ctrl.isDirty:
            break
        time.sleep(0.01)
    assert md_editor_ctrl.isDirty is False
    assert save_btn.property("enabled") is False

    # Modify again to test Ctrl+S shortcut trigger
    text_area.setProperty("text", "# Third Edit Triggered By Shortcut")
    assert md_editor_ctrl.isDirty is True
    assert save_btn.property("enabled") is True

    shortcuts = [obj for obj in editor_root.findChildren(QObject) if "Shortcut" in obj.metaObject().className()]
    assert len(shortcuts) > 0
    sc = shortcuts[0]
    assert sc.property("enabled") is True

    saving_spy = MagicMock()
    md_editor_ctrl.savingChanged.connect(saving_spy)

    # Trigger Save shortcut activation
    sc.activated.emit()
    saving_spy.assert_called_once()

    for _ in range(50):
        QGuiApplication.processEvents()
        if not md_editor_ctrl.isSaving and not md_editor_ctrl.isDirty:
            break
        time.sleep(0.01)

    assert md_editor_ctrl.isDirty is False
    assert save_btn.property("enabled") is False
