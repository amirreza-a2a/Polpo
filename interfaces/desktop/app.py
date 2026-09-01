# ============================================================
#  interfaces/desktop/app.py
#  Canonical Desktop Application Shell & Entrypoint
# ============================================================

import sys
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine
from interfaces.desktop.composition import DesktopAppContainer
from interfaces.desktop.bridge import QtSignalEventBridge
from interfaces.desktop.controllers import (
    JobController,
    ApiKeyController,
    PromptController,
    SettingsController,
    QuickConvertController,
)
from interfaces.desktop.models import (
    JobQueueModel,
    JobHistoryModel,
    ApiSlotModel,
    PromptListModel,
)


def create_app(
    argv: Optional[List[str]] = None,
    db_path: Optional[str | Path] = None,
    artifacts_dir: Optional[str | Path] = None,
    vault_path: Optional[str | Path] = None,
    passphrase: Optional[str] = None,
    keyring_service_name: str = "polpot_desktop",
    default_rpms: Optional[Dict[str, int]] = None,
    scheduler_tick_interval: float = 5.0,
    start_background_runtime: bool = True,
) -> Tuple[QGuiApplication, QQmlApplicationEngine, DesktopAppContainer]:
    """
    Constructs and wires the full PySide6 / QML desktop application:
    1. Instantiates QGuiApplication.
    2. Instantiates DesktopAppContainer.
    3. Runs deterministic initialization (schema migrations, stale recovery, missed schedules).
    4. Attaches QtSignalEventBridge to InMemoryEventBus.
    5. Instantiates Controllers and ViewModels.
    6. Registers context properties in QQmlApplicationEngine root context.
    7. Starts background runtime and scheduler.
    8. Wires graceful teardown to aboutToQuit signal.
    """
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PolpoT")
    app.setOrganizationName("PolpoT")

    # 1. Dependency injection composition root
    container = DesktopAppContainer(
        db_path=db_path,
        artifacts_dir=artifacts_dir,
        vault_path=vault_path,
        passphrase=passphrase,
        keyring_service_name=keyring_service_name,
        default_rpms=default_rpms,
        scheduler_tick_interval=scheduler_tick_interval,
    )

    # 2. Sequential startup reconciliation before UI rendering
    container.initialize()

    # 3. Thread-safe Qt signal bridge
    bridge = QtSignalEventBridge(container.event_bus)

    # 4. Presentation Controllers
    job_controller = JobController(
        submission_service=container.job_submission_service,
        execution_service=container.job_execution_service,
        schedule_service=container.schedule_service,
        recovery_service=container.job_recovery_service,
        artifact_service=container.artifact_service,
        query_service=container.job_query_service,
    )
    api_key_controller = ApiKeyController(
        api_key_service=container.api_key_service,
    )
    prompt_controller = PromptController(
        prompt_service=container.prompt_service,
    )
    settings_controller = SettingsController(
        settings_service=container.settings_service,
        artifact_service=container.artifact_service,
    )
    quick_convert_controller = QuickConvertController(
        quick_convert_service=container.quick_convert_service,
    )

    # 5. QAbstractListModel ViewModels
    job_queue_model = JobQueueModel(
        query_service=container.job_query_service,
        bridge=bridge,
    )
    job_history_model = JobHistoryModel(
        query_service=container.job_query_service,
        bridge=bridge,
    )
    api_slot_model = ApiSlotModel(
        api_key_service=container.api_key_service,
        controller=api_key_controller,
    )
    prompt_list_model = PromptListModel(
        prompt_service=container.prompt_service,
        controller=prompt_controller,
    )

    # Attach presentation objects to container for clean access
    container.bridge = bridge
    container.job_controller = job_controller
    container.api_key_controller = api_key_controller
    container.prompt_controller = prompt_controller
    container.settings_controller = settings_controller
    container.quick_convert_controller = quick_convert_controller
    container.job_queue_model = job_queue_model
    container.job_history_model = job_history_model
    container.api_slot_model = api_slot_model
    container.prompt_list_model = prompt_list_model

    # 6. QML Engine & Root Context Exposure
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("jobController", job_controller)
    ctx.setContextProperty("apiKeyController", api_key_controller)
    ctx.setContextProperty("promptController", prompt_controller)
    ctx.setContextProperty("settingsController", settings_controller)
    ctx.setContextProperty("quickConvertController", quick_convert_controller)
    ctx.setContextProperty("jobQueueModel", job_queue_model)
    ctx.setContextProperty("jobHistoryModel", job_history_model)
    ctx.setContextProperty("apiSlotModel", api_slot_model)
    ctx.setContextProperty("promptListModel", prompt_list_model)
    ctx.setContextProperty("eventBridge", bridge)

    # 7. Start background execution runtime and scheduler
    if start_background_runtime:
        container.start_runtime()

    # 8. Graceful shutdown handler
    def on_shutdown():
        container.shutdown()
        bridge.detach()

    app.aboutToQuit.connect(on_shutdown)

    return app, engine, container


def main() -> int:
    """CLI entrypoint for starting the desktop GUI application."""
    app, engine, container = create_app()
    qml_path = Path(__file__).parent / "qml" / "Main.qml"
    if qml_path.exists():
        engine.load(str(qml_path))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
