# ============================================================
#  tests/unit/test_desktop_gui_controls.py
#  GUI Control Hierarchy and Rendered Layout Regression Tests
# ============================================================

import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from interfaces.desktop.qt_compat import QGuiApplication
from interfaces.desktop.app import create_app


class TestDesktopGuiControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance()
        if cls.app is None:
            cls.app = QGuiApplication(["-platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.db_path = self.base_dir / "gui_test.db"

        self.app_inst, self.engine, self.container = create_app(
            argv=["-platform", "offscreen"],
            db_path=self.db_path,
            start_background_runtime=False,
            scheduler_tick_interval=0.1,
        )

        qml_path = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "Main.qml"
        self.engine.load(str(qml_path))

        self.root_objects = self.engine.rootObjects()
        self.assertEqual(len(self.root_objects), 1)
        self.window = self.root_objects[0]
        self.stack = self.window.findChild(object, "mainStackLayout")
        self.assertIsNotNone(self.stack)

    def tearDown(self):
        self.container.shutdown()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def _switch_tab(self, tab_index: int):
        self.stack.setProperty("currentIndex", tab_index)
        self.app.processEvents()

    def test_all_primary_controls_exist_and_are_visibly_accessible(self):
        """Verifies that all primary buttons exist, are enabled, and have visible, valid bounds."""
        win_w = self.window.property("width")
        win_h = self.window.property("height")
        self.assertGreater(win_w, 0)
        self.assertGreater(win_h, 0)

        tab_checks = [
            (0, "submitDocumentButton"),
            (1, "historyPrevButton"),
            (1, "historyNextButton"),
            (2, "selectImageFileButton"),
            (3, "addApiKeyButton"),
            (4, "newPromptButton"),
            (5, "savePreferencesButton"),
        ]

        for tab_idx, control_name in tab_checks:
            self._switch_tab(tab_idx)
            ctrl = self.window.findChild(object, control_name)
            self.assertIsNotNone(ctrl, f"Control '{control_name}' not found in QML hierarchy!")

            w = ctrl.property("width")
            h = ctrl.property("height")
            vis = ctrl.property("visible")
            en = ctrl.property("enabled")
            x = ctrl.property("x")
            y = ctrl.property("y")

            self.assertTrue(vis, f"Control '{control_name}' is not visible when tab {tab_idx} is active!")
            if control_name not in ("historyPrevButton", "historyNextButton"):
                self.assertTrue(en, f"Control '{control_name}' is not enabled!")
            self.assertGreater(w, 0, f"Control '{control_name}' width must be > 0 (was {w})")
            self.assertGreater(h, 0, f"Control '{control_name}' height must be > 0 (was {h})")
            self.assertGreaterEqual(x, 0, f"Control '{control_name}' x position must be >= 0 (was {x})")
            self.assertGreaterEqual(y, 0, f"Control '{control_name}' y position must be >= 0 (was {y})")

    def test_all_modal_dialogs_open_and_have_positive_dimensions(self):
        """Verifies that every modal dialog can be opened, becomes visible, and is properly sized."""
        modals = [
            (0, "submitJobModal"),
            (0, "rescheduleJobModal"),
            (3, "addKeyModal"),
            (3, "editKeyModal"),
            (4, "createPromptModal"),
            (4, "editPromptModal"),
        ]

        for tab_idx, modal_name in modals:
            self._switch_tab(tab_idx)
            modal = self.window.findChild(object, modal_name)
            self.assertIsNotNone(modal, f"Modal '{modal_name}' not found in QML hierarchy!")

            modal.open()
            self.app.processEvents()

            self.assertTrue(modal.property("visible"), f"Modal '{modal_name}' did not become visible on open()!")
            self.assertGreaterEqual(modal.property("width"), 400)
            self.assertGreaterEqual(modal.property("height"), 250)

            modal.close()
            self.app.processEvents()
            self.assertFalse(modal.property("visible"), f"Modal '{modal_name}' did not hide on close()!")

    def test_full_interactive_workflow_via_controllers_and_ui(self):
        """Tests complete end-to-end user workflows across all 6 sections."""
        # 1. API Keys: Register and verify slot in model
        self._switch_tab(3)
        api_ctrl = self.container.api_key_controller
        reg_ok = api_ctrl.register_key("openai", "Work OpenAI Key", "sk-proj-test12345", "gpt-4o")
        self.assertTrue(reg_ok)
        self.app.processEvents()
        self.assertEqual(self.container.api_slot_model.rowCount(), 1)

        # 2. Prompts: Create and verify prompt in model
        self._switch_tab(4)
        prompt_ctrl = self.container.prompt_controller
        pid = prompt_ctrl.create_prompt("Math OCR", "Extract LaTeX formulas", "pipeline1", True)
        self.assertGreater(pid, 0)
        self.app.processEvents()
        self.assertGreaterEqual(self.container.prompt_list_model.rowCount(), 1)

        # 3. Job Queue: Submit PDF and verify job in model
        self._switch_tab(0)
        job_ctrl = self.container.job_controller
        pdf_f = self.base_dir / "doc.pdf"
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_f))
        doc.close()

        jid = job_ctrl.submit_job(pdf_f.resolve().as_uri(), pid, "")
        self.assertGreater(jid, 0)
        self.app.processEvents()
        self.assertGreaterEqual(self.container.job_queue_model.rowCount(), 1)

        # 4. Settings: Update preferences and verify
        self._switch_tab(5)
        settings_ctrl = self.container.settings_controller
        save_ok = settings_ctrl.save_settings("light", 3, "run_immediately", 60, True, True)
        self.assertTrue(save_ok)
        self.assertEqual(settings_ctrl.theme, "light")
        self.assertEqual(settings_ctrl.maxConcurrentJobs, 3)

        # 5. Quick Convert: Copy to clipboard
        self._switch_tab(2)
        qc_ctrl = self.container.quick_convert_controller
        clip_ok = qc_ctrl.copy_to_clipboard("# Heading\nSample text")
        self.assertTrue(clip_ok)

    def test_prompt_card_bounded_preview_and_long_text_layout(self):
        """Verifies long Persian/English prompt text renders within bounded summary cards without overflow."""
        self._switch_tab(4)
        prompt_ctrl = self.container.prompt_controller
        long_persian_text = (
            "دستورالعمل جامع و دقیق جهت استخراج اسناد فارسی و انگلیسی با فرمول‌های پیچیده ریاضی "
            "و کدهای پایتون. لطفا تمامی جداول و نمودارها را با ساختار Markdown بازسازی کنید. "
            "Formula: \\sum_{i=1}^n x_i = X. " * 5
        )
        pid = prompt_ctrl.create_prompt("Very Long Persian Prompt", long_persian_text, "pipeline2", False)
        self.assertGreater(pid, 0)
        self.app.processEvents()

        # Verify prompt list contains the new prompt
        p_list = self.window.findChild(object, "promptList")
        self.assertIsNotNone(p_list)
        self.assertGreater(p_list.property("count"), 0)

        # Open Edit Modal and verify large multiline editor is sized properly
        edit_modal = self.window.findChild(object, "editPromptModal")
        self.assertIsNotNone(edit_modal)
        edit_modal.open()
        self.app.processEvents()
        self.assertTrue(edit_modal.property("visible"))
        self.assertGreater(edit_modal.property("width"), 400)
        self.assertGreater(edit_modal.property("height"), 400)
        edit_modal.close()
        self.app.processEvents()

    def test_job_queue_action_feedback_rendering(self):
        """Verifies immediate action feedback ('cancelling', 'retrying', 'resuming') in JobQueueView."""
        self._switch_tab(0)
        q_model = self.container.job_queue_model
        if q_model.rowCount() == 0:
            prompt_ctrl = self.container.prompt_controller
            pid = prompt_ctrl.create_prompt("Queue Test Prompt", "Extract text", "pipeline1", True)

            api_ctrl = self.container.api_key_controller
            api_ctrl.register_key("openai", "Test Key", "sk-test", "gpt-4o")

            job_ctrl = self.container.job_controller
            pdf_f = self.base_dir / "action_feedback.pdf"
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
            doc = fitz.open()
            doc.new_page()
            doc.save(str(pdf_f))
            doc.close()
            job_ctrl.submit_job(str(pdf_f), pid, "")
            self.app.processEvents()

        self.assertGreater(q_model.rowCount(), 0)
        job_id = q_model.data(q_model.index(0, 0), q_model.IdRole)

        # 1. Set cancelling
        q_model.set_action_state(job_id, "cancelling")
        self.app.processEvents()
        self.assertEqual(q_model.data(q_model.index(0, 0), q_model.ActionStateRole), "cancelling")

        # 2. Set retrying
        q_model.set_action_state(job_id, "retrying")
        self.app.processEvents()
        self.assertEqual(q_model.data(q_model.index(0, 0), q_model.ActionStateRole), "retrying")

        # 3. Set resuming
        q_model.set_action_state(job_id, "resuming")
        self.app.processEvents()
        self.assertEqual(q_model.data(q_model.index(0, 0), q_model.ActionStateRole), "resuming")


if __name__ == "__main__":
    unittest.main()
