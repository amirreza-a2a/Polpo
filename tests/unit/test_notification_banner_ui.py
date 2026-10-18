# ============================================================
#  tests/unit/test_notification_banner_ui.py
#  UI Presentation Integration Tests for NotificationBanner and PDF Export Error Flow
# ============================================================

from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest


from application.dto.package_recovery_dto import PackageRecoveryPlanDTO
from application.dto.pdf_export_dto import PdfExportResultDTO
from core.exceptions.domain_exceptions import (
    MissingLatexPackageError,
    PdfCompilationError,
)
from interfaces.desktop.app import _teardown_app_container, create_app
from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QQmlApplicationEngine,
    QQmlComponent,
    QQuickItem,
    QObject,
    QUrl,
)


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def pump_events(app, count=5, delay=0.01):
    for _ in range(count):
        app.processEvents()
        if delay > 0:
            time.sleep(delay)


def pump_until(app, condition, timeout=3.0, step=0.02):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        app.processEvents()
        if condition():
            return True
        if step > 0:
            time.sleep(step)
    app.processEvents()
    return bool(condition())


def test_notification_banner_geometry_and_dismissal_lifecycle(qapp, tmp_path):
    """
    Verifies NotificationBanner inside Main.qml:
    1. Zero height and invisible when message is empty.
    2. Non-zero height (> 0) and visible when message is set.
    3. Multiline expansion and text wrapping for long diagnostic messages.
    4. Dismiss action clears the error and does NOT break the QML binding.
    5. A subsequent error updates the banner and restores visibility.
    """
    db_file = tmp_path / "test.db"
    artifacts_dir = tmp_path / "artifacts"
    app, engine, container = create_app(
        argv=["-platform", "offscreen"],
        db_path=db_file,
        artifacts_dir=artifacts_dir,
        start_background_runtime=False,
    )
    qml_path = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "Main.qml"
    ).resolve()

    engine.load(QUrl.fromLocalFile(str(qml_path)))
    pump_events(qapp, count=5)

    root_objs = engine.rootObjects()
    assert len(root_objs) > 0, "Failed to load Main.qml"
    win = root_objs[0]

    # Locate the error NotificationBanner
    error_banner = None
    for c in win.findChildren(QQuickItem):
        if c.property("bannerType") == "error":
            error_banner = c
            break
    assert error_banner is not None, "Error NotificationBanner not found in Main.qml"

    # 1. Initially empty
    assert not error_banner.property("visible")
    assert error_banner.property("implicitHeight") == 0.0
    assert error_banner.property("height") == 0.0

    # 2. Short error message
    win.setProperty("globalError", "Short failure message")
    pump_events(qapp, count=5)

    assert error_banner.property("visible")
    assert error_banner.property("implicitHeight") >= 36.0
    assert error_banner.property("height") >= 36.0
    short_height = error_banner.property("height")

    # 3. Multiline diagnostic message
    long_msg = (
        "LaTeX compilation failed at line 400: Package pdftexcmds Info: \\pdfdraftmode not found.\n"
        "! Undefined control sequence.\n"
        "l.400 \\pdfdraftmode\n"
        "The compiler encountered an unrecoverable error while rendering the document. "
        "Please inspect line 400 in the generated TeX source."
    )
    win.setProperty("globalError", long_msg)
    pump_events(qapp, count=5)

    assert error_banner.property("visible")
    assert error_banner.property("implicitHeight") > short_height
    assert error_banner.property("height") > short_height

    # Verify text wrapping and containment
    text_item = error_banner.findChild(QQuickItem, "notificationMessageText")
    dismiss_btn = error_banner.findChild(QQuickItem, "notificationDismissButton")

    assert text_item is not None, "Text item not found in NotificationBanner"
    assert dismiss_btn is not None, "Dismiss button not found in NotificationBanner"
    assert text_item.property("width") < error_banner.property("width")
    assert text_item.property("height") > 30.0, "Multiline message should produce wrapped text height"

    # 4. Dismiss action
    dismiss_btn.clicked.emit()
    pump_events(qapp, count=5)

    assert win.property("globalError") == "", "Dismiss must clear window.globalError"
    assert error_banner.property("message") == ""
    assert not error_banner.property("visible")

    # 5. Subsequent error after dismissal (binding must still be intact)
    win.setProperty("globalError", "Second failure after dismissal")
    pump_events(qapp, count=5)

    assert error_banner.property("message") == "Second failure after dismissal"
    assert error_banner.property("visible")
    assert error_banner.property("height") >= 36.0

    # 6. Long unbroken token without spaces (must wrap at character boundary)
    unbroken_msg = "LaTeX compilation failed: " + "X" * 150
    win.setProperty("globalError", unbroken_msg)
    pump_events(qapp, count=5)

    assert error_banner.property("visible")
    assert text_item.property("paintedWidth") <= text_item.property("width") + 1.0, (
        f"Text paintedWidth ({text_item.property('paintedWidth')}) exceeded available width ({text_item.property('width')})"
    )

    _teardown_app_container(container)
    win.deleteLater()
    engine.deleteLater()
    pump_events(qapp, count=2)


def test_pdf_export_generic_compilation_error_ui_flow(qapp, tmp_path):
    """
    Verifies that when QuartoPdfExportService raises PdfCompilationError:
    1. exportController.exportFailed is emitted.
    2. Main.qml window.globalError receives the sanitized error message.
    3. The error NotificationBanner becomes visible with non-zero rendered height.
    4. isExporting returns to False.
    """
    db_file = tmp_path / "test.db"
    artifacts_dir = tmp_path / "artifacts"
    app, engine, container = create_app(
        argv=["-platform", "offscreen"],
        db_path=db_file,
        artifacts_dir=artifacts_dir,
        start_background_runtime=False,
    )

    # Mock PDF export service to fail with compilation error
    mock_pdf_service = MagicMock()
    mock_pdf_service.export_pdf.side_effect = PdfCompilationError(
        sanitized_message="LaTeX compilation failed at line 42: ! Undefined control sequence."
    )
    container.export_controller.export_pdf_service = mock_pdf_service

    # Load Main.qml
    qml_path = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "Main.qml"
    ).resolve()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    pump_events(qapp, count=5)

    win = engine.rootObjects()[0]
    error_banner = None
    for c in win.findChildren(QQuickItem):
        if c.property("bannerType") == "error":
            error_banner = c
            break
    assert error_banner is not None

    # Trigger PDF export
    container.export_controller.exportPdf(
        job_id=1,
        destination_uri=str(tmp_path / "out.pdf"),
        version=1,
        overwrite=True,
    )

    # Wait for completion of background export worker
    assert pump_until(qapp, lambda: not container.export_controller.isExporting, timeout=3.0)

    # Assert UI feedback
    assert "LaTeX compilation failed at line 42" in win.property("globalError")
    assert error_banner.property("visible") is True
    assert error_banner.property("height") >= 36.0
    assert error_banner.property("implicitHeight") >= 36.0

    _teardown_app_container(container)
    win.deleteLater()
    engine.deleteLater()
    pump_events(qapp, count=2)


def test_pdf_export_missing_package_recovery_dialog_flow(qapp, tmp_path):
    """
    Verifies that when QuartoPdfExportService raises MissingLatexPackageError:
    1. packageRecoveryRequired is emitted.
    2. ReviewWorkspaceView's MissingPackageRecoveryDialog is opened with package details.
    3. isExporting returns to False.
    """
    db_file = tmp_path / "test.db"
    artifacts_dir = tmp_path / "artifacts"
    app, engine, container = create_app(
        argv=["-platform", "offscreen"],
        db_path=db_file,
        artifacts_dir=artifacts_dir,
        start_background_runtime=False,
    )

    mock_pdf_service = MagicMock()
    mock_pdf_service.export_pdf.side_effect = MissingLatexPackageError(
        package_names=("algorithm2e", "pgfplots"),
        sanitized_message="Missing LaTeX package 'algorithm2e'.",
    )
    container.export_controller.export_pdf_service = mock_pdf_service

    mock_recovery = MagicMock()
    mock_recovery.create_recovery_plan.return_value = PackageRecoveryPlanDTO(
        missing_packages=("algorithm2e", "pgfplots"),
        can_auto_recover=True,
        manual_command="tlmgr install algorithm2e pgfplots",
        diagnostic_summary="TinyTeX auto-recovery available.",
    )
    container.export_controller.package_recovery_service = mock_recovery

    qml_path = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "Main.qml"
    ).resolve()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    pump_events(qapp, count=5)

    win = engine.rootObjects()[0]
    recovery_dialog = None
    for c in win.findChildren(QObject):
        if c.objectName() == "missingPackageRecoveryModal":
            recovery_dialog = c
            break
    assert recovery_dialog is not None, "MissingPackageRecoveryDialog not found"

    # Trigger PDF export
    container.export_controller.exportPdf(
        job_id=1,
        destination_uri=str(tmp_path / "out.pdf"),
        version=1,
        overwrite=True,
    )

    assert pump_until(qapp, lambda: not container.export_controller.isExporting, timeout=3.0)

    # Assert recovery dialog was opened
    assert pump_until(qapp, lambda: recovery_dialog.property("visible"), timeout=2.0)
    pkgs = recovery_dialog.property("missingPackages")
    assert "algorithm2e" in pkgs
    assert recovery_dialog.property("canAutoRecover") is True

    _teardown_app_container(container)
    win.deleteLater()
    engine.deleteLater()
    pump_events(qapp, count=2)


def test_pdf_export_success_ui_flow(qapp, tmp_path):
    """
    Verifies that a successful export leaves error UI clean and resets exporting state.
    """
    db_file = tmp_path / "test.db"
    artifacts_dir = tmp_path / "artifacts"
    app, engine, container = create_app(
        argv=["-platform", "offscreen"],
        db_path=db_file,
        artifacts_dir=artifacts_dir,
        start_background_runtime=False,
    )

    mock_pdf_service = MagicMock()
    mock_pdf_service.export_pdf.return_value = PdfExportResultDTO(
        destination_path=tmp_path / "out.pdf",
        canonical_version=1,
        page_count=4,
        file_size_bytes=4096,
        duration_seconds=0.7,
        cleanup_warning=None,
    )
    container.export_controller.export_pdf_service = mock_pdf_service

    qml_path = (
        Path(__file__).parent.parent.parent
        / "interfaces"
        / "desktop"
        / "qml"
        / "Main.qml"
    ).resolve()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    pump_events(qapp, count=5)

    win = engine.rootObjects()[0]
    error_banner = None
    for c in win.findChildren(QQuickItem):
        if c.property("bannerType") == "error":
            error_banner = c
            break
    # Pre-populate globalError with previous failure
    win.setProperty("globalError", "Stale failure message from previous attempt")
    pump_events(qapp, count=5)
    assert error_banner.property("visible") is True
    assert error_banner.property("height") >= 36.0

    container.export_controller.exportPdf(
        job_id=1,
        destination_uri=str(tmp_path / "out.pdf"),
        version=1,
        overwrite=True,
    )

    # Stale error must be cleared as soon as new export starts
    assert win.property("globalError") == ""
    assert not error_banner.property("visible")

    assert pump_until(qapp, lambda: not container.export_controller.isExporting, timeout=3.0)

    # Must remain clean after successful completion
    assert win.property("globalError") == ""
    assert not error_banner.property("visible")
    assert error_banner.property("height") == 0.0

    _teardown_app_container(container)
    win.deleteLater()
    engine.deleteLater()
    pump_events(qapp, count=2)
