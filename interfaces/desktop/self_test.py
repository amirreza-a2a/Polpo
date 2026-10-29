"""Non-interactive diagnostic self-test runner for package smoke verification.

Validates core identity, packaged asset resolution, writable user data and log
directories, runtime discovery (Pandoc, Node.js), SQLite persistence bootstrap,
and headless QML interface instantiation without requiring interactive desktop
access, API credentials, or outbound network connectivity.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import TextIO
import uuid

from core.version import APP_NAME, __version__, VERSION_INFO
from infrastructure.paths import (
    get_runtime_resource_path,
    get_user_data_dir,
    get_user_log_dir,
)
from infrastructure.markdown.pandoc_binary import PandocBinaryResolver
from infrastructure.math.mathjax_supervisor import resolve_node_executable


SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

REQUIRED_MIGRATIONS = [
    "001_initial_schema.sql",
    "002_visual_regions.sql",
    "003_artifact_version_watermarks.sql",
    "004_publish_intents_and_document_versions.sql",
    "005_pipeline2_source_version.sql",
]


class SelfTestDiagnostic:
    """Manages check execution and formatted console reporting."""

    def __init__(self, output: TextIO | None = None):
        self.output = output if output is not None else (sys.stdout if sys.stdout is not None else sys.stderr)
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def log(self, message: str) -> None:
        if self.output is not None:
            self.output.write(message + "\n")
            self.output.flush()

    def pass_check(self, title: str, detail: str = "") -> None:
        suffix = f" ({detail})" if detail else ""
        self.log(f"  [PASS] {title}{suffix}")

    def fail_check(self, title: str, error: str) -> None:
        self.failures.append(f"{title}: {error}")
        self.log(f"  [FAIL] {title}: {error}")

    def info(self, title: str, detail: str) -> None:
        self.log(f"  [INFO] {title}: {detail}")

    def warn(self, title: str, detail: str) -> None:
        self.warnings.append(f"{title}: {detail}")
        self.log(f"  [WARN] {title}: {detail}")


def run_self_test(output: TextIO | None = None) -> int:
    """Execute complete non-interactive diagnostic suite.

    Returns:
        0 if all required checks pass, 1 if any required check fails.
    """
    diag = SelfTestDiagnostic(output)
    diag.log("=" * 64)
    diag.log(f"Polpo Desktop Diagnostic Self-Test")
    diag.log(f"Authoritative Product: {APP_NAME} {__version__}")
    diag.log("=" * 64)

    # 1. Version and Authoritative Metadata Check
    try:
        if not APP_NAME or APP_NAME != "Polpo":
            diag.fail_check("Core identity", f"Expected 'Polpo', got '{APP_NAME}'")
        elif not SEMVER_PATTERN.match(__version__):
            diag.fail_check("Core version", f"'{__version__}' is not valid SemVer 2.0")
        elif not isinstance(VERSION_INFO, tuple) or len(VERSION_INFO) < 3:
            diag.fail_check("Version tuple", f"Invalid VERSION_INFO: {VERSION_INFO}")
        else:
            diag.pass_check("Authoritative application identity and SemVer metadata", f"{APP_NAME} {__version__}")
    except Exception as exc:
        diag.fail_check("Core version import", str(exc))

    # 2. Critical Packaged Asset Resolution
    critical_assets = [
        ("Main.qml interface", "interfaces/desktop/qml/Main.qml"),
        ("LaTeX typesetting preamble", "resources/templates/polpo_preamble.tex"),
        ("Runtimes acquisition manifest", "resources/runtimes.json"),
        ("MathJax worker script", "resources/mathjax/mathjax_worker.js"),
    ]

    all_assets_ok = True
    for label, rel_path in critical_assets:
        try:
            asset_path = get_runtime_resource_path(rel_path)
            if not asset_path.is_file():
                diag.fail_check(f"Packaged asset ({label})", f"File not found at resolved path: {asset_path}")
                all_assets_ok = False
            else:
                # Ensure readable
                _ = asset_path.read_bytes()[:128]
        except Exception as exc:
            diag.fail_check(f"Packaged asset ({label})", str(exc))
            all_assets_ok = False

    # SQLite schema migrations
    try:
        migrations_dir = get_runtime_resource_path("infrastructure/persistence/sqlite/migrations")
        if not migrations_dir.is_dir():
            diag.fail_check("SQLite migrations directory", f"Directory not found: {migrations_dir}")
            all_assets_ok = False
        else:
            for sql_file in REQUIRED_MIGRATIONS:
                sql_path = migrations_dir / sql_file
                if not sql_path.is_file():
                    diag.fail_check(f"Migration asset ({sql_file})", f"Missing migration: {sql_path}")
                    all_assets_ok = False
                else:
                    _ = sql_path.read_text(encoding="utf-8")[:64]
    except Exception as exc:
        diag.fail_check("SQLite migrations asset check", str(exc))
        all_assets_ok = False

    if all_assets_ok:
        diag.pass_check("Critical packaged resources and SQL migrations resolution", "All assets readable")

    # 3. Cross-Platform User Data & Log Directories
    try:
        data_dir = get_user_data_dir()
        log_dir = get_user_log_dir()

        data_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Non-destructive writability probe
        probe_token = uuid.uuid4().hex
        data_probe = data_dir / f".selftest_probe_{probe_token}.tmp"
        try:
            data_probe.write_text("ok", encoding="utf-8")
            _ = data_probe.read_text(encoding="utf-8")
        finally:
            if data_probe.exists():
                data_probe.unlink()

        log_probe = log_dir / f".selftest_probe_{probe_token}.tmp"
        try:
            log_probe.write_text("ok", encoding="utf-8")
            _ = log_probe.read_text(encoding="utf-8")
        finally:
            if log_probe.exists():
                log_probe.unlink()

        diag.pass_check("User-data and log directories writable", f"Data: {data_dir}, Logs: {log_dir}")
    except Exception as exc:
        diag.fail_check("User data directory access", str(exc))

    # 4. Runtime Dependency Discovery
    is_frozen = getattr(sys, "frozen", False)

    # Pandoc Discovery & AST Parse Execution
    pandoc_resolver = PandocBinaryResolver()
    bundled_pandoc = pandoc_resolver.get_bundled_path()
    system_pandoc = pandoc_resolver.get_system_path()
    active_pandoc = None

    if bundled_pandoc is not None and bundled_pandoc.is_file():
        diag.pass_check("Pandoc runtime discovery", f"Bundled: {bundled_pandoc}")
        active_pandoc = bundled_pandoc
    elif system_pandoc is not None and system_pandoc.is_file():
        if is_frozen:
            diag.fail_check(
                "Pandoc runtime discovery",
                f"Frozen build requires bundled Pandoc but found only system Pandoc at {system_pandoc}",
            )
        else:
            diag.pass_check("Pandoc runtime discovery", f"System PATH: {system_pandoc}")
            active_pandoc = system_pandoc
    else:
        if is_frozen:
            diag.fail_check("Pandoc runtime discovery", "Pandoc executable not found (required for frozen bundle)")
        else:
            diag.warn(
                "Pandoc runtime discovery",
                "Pandoc not found in bundled resources or system PATH (unbundled build; provisioning in TICK-REL-04)",
            )

    if active_pandoc is not None:
        try:
            from infrastructure.markdown.pandoc_runner import PandocRunner
            runner = PandocRunner(binary_path=active_pandoc)
            ast = runner.run("# تست عنوان\n\n$E=mc^2$")
            if "pandoc-api-version" in ast:
                diag.pass_check("Pandoc Markdown AST parse execution", f"AST validated (API {ast.get('pandoc-api-version')})")
            else:
                diag.fail_check("Pandoc Markdown AST parse execution", "Output missing 'pandoc-api-version'")
        except Exception as exc:
            if is_frozen:
                diag.fail_check("Pandoc Markdown AST parse execution", str(exc))
            else:
                diag.warn("Pandoc Markdown AST parse execution", str(exc))

    # Node.js Discovery & MathJax SVG Render Execution
    try:
        node_path = resolve_node_executable()
        ext = ".exe" if (os.name == "nt" or sys.platform == "win32") else ""
        bundled_node = get_runtime_resource_path(f"resources/mathjax/node{ext}")
        is_node_bundled = bundled_node.is_file() and (node_path.resolve() == bundled_node.resolve())

        if is_node_bundled:
            diag.pass_check("Node.js runtime discovery", f"Bundled: {node_path}")
        else:
            if is_frozen:
                diag.fail_check(
                    "Node.js runtime discovery",
                    f"Frozen build requires bundled Node.js but found system binary at {node_path}",
                )
            else:
                diag.pass_check("Node.js runtime discovery", f"System PATH: {node_path}")

        # Execute MathJax daemon formula rendering
        try:
            from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor
            supervisor = MathJaxProcessSupervisor()
            try:
                render_res = supervisor.render(tex="E=mc^2", display=False)
                svg_markup = render_res.get("svg_xml", "")
                if render_res and "<svg" in svg_markup:
                    diag.pass_check("MathJax vector SVG render execution", f"Rendered <svg> ({len(svg_markup)} bytes)")
                else:
                    diag.fail_check("MathJax vector SVG render execution", "Output missing valid <svg> markup")
            finally:
                supervisor.shutdown()
        except Exception as exc:
            if is_frozen:
                diag.fail_check("MathJax vector SVG render execution", str(exc))
            else:
                diag.warn("MathJax vector SVG render execution", f"MathJax render unverified: {exc}")
    except Exception as exc:
        if is_frozen:
            diag.fail_check("Node.js runtime discovery", str(exc))
        else:
            diag.warn(
                "Node.js runtime discovery",
                f"Node.js not resolved: {exc} (provisioning in TICK-REL-04)",
            )

    # Optional PDF Export Prerequisites (Informational only)
    quarto_exe = shutil.which("quarto")
    diag.info("Optional runtime (Quarto)", quarto_exe or "Not installed (optional for PDF export)")

    xelatex_exe = shutil.which("xelatex")
    diag.info("Optional runtime (XeLaTeX)", xelatex_exe or "Not installed (optional for PDF export)")

    # 5. Local SQLite Persistence & Clean Architecture Bootstrap Smoke Test
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            from core.entities.artifact import ArtifactType
            from core.entities.credential_ref import CredentialRef
            from interfaces.desktop.composition import DesktopAppContainer
            from infrastructure.pdf.staging_workspace import StagingWorkspaceManager

            staging_mgr = StagingWorkspaceManager()
            preamble_file = staging_mgr.templates_dir / "polpo_preamble.tex"
            if not preamble_file.is_file():
                diag.fail_check("LaTeX preamble template staging", f"File not found: {preamble_file}")
            else:
                diag.pass_check("LaTeX preamble template staging", f"Resolved: {preamble_file}")

            container = DesktopAppContainer(
                db_path=tmp_root / "selftest_smoke.db",
                artifacts_dir=tmp_root / "artifacts",
                vault_path=tmp_root / "vault.enc",
                passphrase="SelfTestCanaryPassphrase123!",
                scheduler_tick_interval=100.0,
            )
            try:
                container.initialize()

                # Verify schema tables created
                conn = container.db_manager.create_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    tables = {row[0] for row in cursor.fetchall()}
                    expected_tables = {
                        "schema_version",
                        "jobs",
                        "prompts",
                        "api_slots",
                        "app_settings",
                        "pipeline2_jobs",
                        "visual_regions",
                        "publish_intents",
                        "document_versions",
                    }
                    missing_tables = expected_tables - tables
                    if missing_tables:
                        diag.fail_check("SQLite migration verification", f"Missing tables: {missing_tables}")
                    else:
                        diag.pass_check(
                            "SQLite persistence and migrations bootstrap",
                            f"All {len(expected_tables)} required tables verified ({len(tables)} total)",
                        )

                    # Verify default prompt seeding per Section 7.3 & TICK-REL-03
                    cursor.execute("SELECT COUNT(*) FROM prompts;")
                    prompt_count = cursor.fetchone()[0]
                    if prompt_count < 9:
                        diag.fail_check("Default prompt seeding", f"Expected at least 9 seeded prompts, got {prompt_count}")
                    else:
                        diag.pass_check("Default prompt seeding bootstrap", f"All {prompt_count} curated prompt templates seeded")
                finally:
                    conn.close()

                # Verify UnitOfWork repository access and default settings
                with container.uow_factory.create() as uow:
                    settings = uow.settings.get()
                    if settings is None:
                        diag.fail_check("UnitOfWork repository access", "Default AppSettings not found via UnitOfWork")
                    else:
                        diag.pass_check(
                            "UnitOfWork repository access",
                            f"AppSettings resolved (max_concurrent_jobs={settings.max_concurrent_jobs})",
                        )

                # Verify synthetic canary credential isolation and resolution
                canary_key = "selftest_secret_token_abcdef123456"
                canary_ref_id = "selftest_canary_slot"
                container.encrypted_store.store_secret(canary_ref_id, canary_key)
                resolved_key = container.credential_resolver.resolve_api_key(
                    CredentialRef(canary_ref_id, "test_provider", "encrypted_store")
                )
                if resolved_key != canary_key:
                    diag.fail_check("Credential isolation and resolution", "Resolved canary key did not match stored key")
                else:
                    # Verify no raw secrets leaked into SQLite database
                    leak_conn = container.db_manager.create_connection()
                    try:
                        leak_cur = leak_conn.cursor()
                        leak_cur.execute("SELECT * FROM api_slots;")
                        slots_dump = str(leak_cur.fetchall())
                        if canary_key in slots_dump:
                            diag.fail_check("Credential isolation and resolution", "Raw canary secret leaked into api_slots table")
                        else:
                            diag.pass_check(
                                "Credential isolation and resolution",
                                "Synthetic canary stored and resolved cleanly without database leakage",
                            )
                    finally:
                        leak_conn.close()

                # Verify local artifact storage roundtrip
                artifact_probe_payload = b"# Polpo Self-Test Artifact Storage Probe\n"
                handle = container.storage.store(
                    job_id=99999,
                    artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                    filename="selftest_probe.md",
                    data=artifact_probe_payload,
                )
                retrieved_payload = container.storage.retrieve(handle)
                if retrieved_payload != artifact_probe_payload:
                    diag.fail_check("Artifact storage roundtrip", "Retrieved payload does not match stored bytes")
                else:
                    diag.pass_check("Artifact storage roundtrip", f"{len(retrieved_payload)} bytes stored and retrieved cleanly")
            finally:
                container.shutdown()
    except Exception as exc:
        diag.fail_check("Persistence smoke test", str(exc))

    # 6. Headless QML Engine & Root Object Instantiation Smoke Test
    try:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            from interfaces.desktop.app import create_app

            container = None
            try:
                app, engine, container = create_app(
                    argv=["polpo", "-platform", "offscreen"],
                    db_path=tmp_root / "qml_selftest.db",
                    artifacts_dir=tmp_root / "artifacts",
                    vault_path=tmp_root / "vault.enc",
                    start_background_runtime=False,
                )

                qml_errors: list[str] = []
                engine.warnings.connect(lambda warns: [qml_errors.append(w.toString()) for w in warns])

                qml_path = get_runtime_resource_path("interfaces/desktop/qml/Main.qml")
                engine.load(str(qml_path))
                root_objects = engine.rootObjects()

                if not root_objects:
                    error_detail = (": " + "; ".join(qml_errors)) if qml_errors else ""
                    diag.fail_check(
                        "QML root object instantiation",
                        f"QQmlApplicationEngine produced 0 root objects from {qml_path}{error_detail}",
                    )
                else:
                    diag.pass_check("QML interface compilation and root object instantiation", f"{len(root_objects)} root object instantiated")
            finally:
                if container is not None:
                    container.shutdown()
    except Exception as exc:
        diag.fail_check("Headless QML instantiation", str(exc))

    # Summary and Exit Status
    diag.log("-" * 64)
    if diag.failures:
        diag.log(f"Self-test FAILED with {len(diag.failures)} error(s):")
        for fail in diag.failures:
            diag.log(f"  - {fail}")
        diag.log("=" * 64)
        return 1

    diag.log("Self-test PASSED: All required runtime and packaging invariants verified.")
    diag.log("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(run_self_test())
