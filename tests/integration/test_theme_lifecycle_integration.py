# ============================================================
#  tests/integration/test_theme_lifecycle_integration.py
#  Integration tests for Full Desktop Theme Lifecycle (TICK-P01C)
# ============================================================

from pathlib import Path
import tempfile
from unittest.mock import MagicMock
import pytest

from interfaces.desktop.app import create_app
from interfaces.desktop.qt_compat import QGuiApplication, QTextDocument
from interfaces.desktop.syntax.markdown_syntax_highlighter import (
    DARK_SYNTAX_PALETTE,
    LIGHT_SYNTAX_PALETTE,
)


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def test_theme_lifecycle_end_to_end(qapp):
    """
    Validates complete end-to-end theme lifecycle:
    1. Settings batch save -> SQLite commit
    2. SettingsController.settings_changed signal
    3. ThemeController.set_theme_preference slot
    4. ThemeController.resolvedThemeChanged signal
    5. MarkdownEditorController.set_syntax_theme slot
    6. Root context QML property 'theme' reflects updated palette
    7. Persistence survives application restart (SQLite source of truth)
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        db_path = base / "theme_lifecycle.db"

        # Step 1: Boot application with fresh SQLite database
        app, engine, container = create_app(
            argv=[],
            db_path=db_path,
            start_background_runtime=False,
            scheduler_tick_interval=10.0,
        )

        try:
            settings_ctrl = container.settings_controller
            theme_ctrl = container.theme_controller
            editor_ctrl = container.markdown_editor_controller

            # Verify QML root context property 'theme' is registered and points to theme_ctrl
            root_ctx = engine.rootContext()
            assert root_ctx.contextProperty("theme") == theme_ctrl

            # Verify initial state: default preference is system, headless resolves to dark
            assert theme_ctrl.themePreference in ("system", "dark")
            assert theme_ctrl.resolvedTheme == "dark"
            assert theme_ctrl.background == "#0d1117"
            assert editor_ctrl._syntax_theme == "dark"

            # Attach a document to editor controller
            doc = QTextDocument()
            doc.setPlainText("# Section 1\n`inline code`")
            mock_quick_doc = MagicMock()
            mock_quick_doc.textDocument.return_value = doc
            editor_ctrl.attachTextDocument(mock_quick_doc)

            assert editor_ctrl._highlighter is not None
            assert editor_ctrl._highlighter.current_theme == "dark"
            b0 = doc.findBlockByNumber(0)
            assert b0.layout().formats()[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]

            # Step 2: User changes theme to 'light' in settings view and saves preferences
            settings_ctrl.save_settings(
                theme="light",
                max_concurrent_jobs=2,
                missed_schedule_policy="prompt",
                artifact_retention_days=30,
                auto_retry=True,
                auto_pipeline2=False,
            )

            # Step 3: Verify signals propagated and all layers updated to 'light'
            assert settings_ctrl.theme == "light"
            assert theme_ctrl.themePreference == "light"
            assert theme_ctrl.resolvedTheme == "light"
            assert theme_ctrl.background == "#ffffff"
            assert theme_ctrl.textPrimary == "#1f2328"
            assert editor_ctrl._syntax_theme == "light"
            assert editor_ctrl._highlighter.current_theme == "light"

            # Check that editor document text was immediately rehighlighted with light palette
            b0_light = doc.findBlockByNumber(0)
            assert b0_light.layout().formats()[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["headings"][0]

            # Step 4: User changes theme to 'dark'
            settings_ctrl.save_settings(
                theme="dark",
                max_concurrent_jobs=2,
                missed_schedule_policy="prompt",
                artifact_retention_days=30,
                auto_retry=True,
                auto_pipeline2=False,
            )

            assert settings_ctrl.theme == "dark"
            assert theme_ctrl.themePreference == "dark"
            assert theme_ctrl.resolvedTheme == "dark"
            assert theme_ctrl.background == "#0d1117"
            assert editor_ctrl._syntax_theme == "dark"
            assert editor_ctrl._highlighter.current_theme == "dark"

            b0_dark = doc.findBlockByNumber(0)
            assert b0_dark.layout().formats()[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]

        finally:
            # Teardown first app instance
            container.mathjax_supervisor.shutdown()
            container.markdown_viewer_controller.shutdown()
            container.document_viewer_controller.shutdown()
            container.markdown_editor_controller.shutdown()
            container.export_controller.shutdown()
            container.scheduler.shutdown()
            container.runtime.shutdown()

        # Step 5: Application restart verification (persistence recovery)
        # Reopen app with same database file to prove settings survived restart
        app2, engine2, container2 = create_app(
            argv=[],
            db_path=db_path,
            start_background_runtime=False,
            scheduler_tick_interval=10.0,
        )

        try:
            assert container2.settings_service.get_settings().theme == "dark"
            assert container2.theme_controller.themePreference == "dark"
            assert container2.theme_controller.resolvedTheme == "dark"
            assert container2.theme_controller.background == "#0d1117"
            assert container2.markdown_editor_controller._syntax_theme == "dark"
        finally:
            container2.mathjax_supervisor.shutdown()
            container2.markdown_viewer_controller.shutdown()
            container2.document_viewer_controller.shutdown()
            container2.markdown_editor_controller.shutdown()
            container2.export_controller.shutdown()
            container2.scheduler.shutdown()
            container2.runtime.shutdown()
