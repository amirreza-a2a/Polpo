# Polpo Packaging and Cross-Platform Distribution Guide

This document defines the packaging, runtime bundling, and code-signing architecture for Polpo Desktop across Linux, Windows, and macOS target environments (per AGENTS.md Rule 28 and Phase 12 Architecture Wayfinder).

---

## 1. Runtime Bundling Architecture

Polpo is a desktop-first, local-first, serverless application. All parsing (Pandoc) and math vector rendering (MathJax via headless Node.js daemon) execute entirely locally on the user's workstation without remote backend servers or cloud APIs.

To ensure hermetic offline execution without requiring end-users to manually install Pandoc or Node.js, Polpo packages standalone binaries within the application bundle:

```text
Polpo/
├── resources/
│   ├── runtimes.json                # Upstream acquisition manifest with SHA-256 hashes
│   ├── bin/
│   │   ├── linux-x86_64/pandoc      # Statically linked musl binary (v3.1.11)
│   │   ├── windows-x64/pandoc.exe   # Standalone Windows executable (v3.1.11)
│   │   ├── darwin-x86_64/pandoc     # macOS x86_64 binary (v3.1.11)
│   │   └── darwin-arm64/pandoc      # macOS Apple Silicon binary (v3.1.11)
│   └── mathjax/
│       ├── node                     # Hermetic Node.js 22.23.2 LTS executable (or node.exe)
│       ├── mathjax_worker.js        # Line-delimited JSON-RPC headless renderer
│       ├── package.json             # Pinned engines & dependencies (mathjax-full@3.2.2)
│       ├── package-lock.json        # Deterministic npm lockfile
│       └── node_modules/            # Bundled production dependencies
```

---

## 2. Binary Discovery Hierarchy

All runtime components resolve binaries using a strict 3-tier precedence hierarchy implemented in `infrastructure/paths.py`:

1. **Bundled Resource Path:**
   Checked first via `get_runtime_resource_path()`. In frozen desktop distributions (PyInstaller `sys._MEIPASS` or Briefcase app root), points directly into the extracted/bundled resources directory. In development checkouts, points to the repository root.
2. **User-Configured Override:**
   If specified in `AppSettings` (e.g. `pandoc_executable_path` or `POLPO_NODE_PATH`), allows advanced users or custom packaging environments to redirect to custom binary installations.
3. **System `PATH` Fallback:**
   Resolved via `shutil.which("pandoc")` or `shutil.which("node")` as a resilient fallback when bundled binaries are not present.

---

## 3. Platform-Specific Execution Considerations

### 3.1 Linux (POSIX)

* **Permission Hardening:** AppImage extraction or tarball unpacking may drop the POSIX executable bit (`+x`). `PandocBinaryResolver` automatically checks `os.access(path, os.X_OK)` and invokes `os.chmod(path, current_mode | 0o111)` if execution permissions are missing.
* **Static Linkage vs. Application Libc Baseline:** While the bundled Linux Pandoc binary is statically linked against musl libc (eliminating external dependencies for that individual executable), the **complete Polpo desktop application** bundle is built against glibc and strictly requires `glibc >= 2.35` on `x86_64` CPU architecture. Musl-based distributions (e.g., Alpine Linux) and non-x86_64 architectures are unsupported and are rejected at installer preflight.
* **System GUI Dependencies:** PySide6 requires standard desktop graphic libraries on the host system: `libgl1`, `libegl1`, `libxkbcommon-x11-0`, and `libdbus-1-3`.

### 3.2 Windows (Win32 / NTFS)

* **File Extensions & Path Separators:** Binaries must include the `.exe` extension (`pandoc.exe`, `node.exe`). Paths must be manipulated using `pathlib.Path` to preserve platform-appropriate backslash separators (`\`).
* **Hidden Console Windows:** Background worker subprocesses (`PandocRunner` and `MathJaxProcessSupervisor`) must pass `creationflags=subprocess.CREATE_NO_WINDOW` (`0x08000000`) on Windows to suppress flickering console windows when launching helper binaries.
* **Process Termination:** Windows lacks POSIX `SIGTERM`. `MathJaxProcessSupervisor` uses line-delimited `{"method": "shutdown"}` over stdin followed by `proc.terminate()` and `proc.kill()` for cooperative teardown without orphaned processes.

---

## 4. macOS Developer ID Code Signing & Notarization

> [!NOTE]
> **Build Guidance Only:** The following code signing and notarization procedure is documented as packaging guidance for future macOS distributions. No signed or notarized macOS release package is currently distributed; macOS is validated via source test suites in periodic CI.

On macOS 10.15+ (Catalina through macOS 15+ Sequoia), Apple Gatekeeper requires all distributed binaries, dynamic libraries, and helper tools to be signed with an Apple Developer ID Application certificate and notarized by Apple.

### 4.1 Entitlements (`resources/entitlements.plist`)

Node.js and PySide6 require specific hardened runtime entitlements:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Required for Node.js V8 JIT compilation -->
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <!-- Required for dynamic library loading by PySide6 / Python plugins -->
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
```

### 4.2 Inside-Out Code Signing Procedure

macOS bundles must be signed **inside-out** (all nested helper executables and `.dylib` files must be signed before the outer `.app` bundle):

1. **Sign Helper Executables with Hardened Runtime:**
   ```bash
   # Sign bundled Pandoc binary
   codesign --force --timestamp --options runtime \
     --entitlements resources/entitlements.plist \
     --sign "Developer ID Application: Your Name (TEAM_ID)" \
     resources/bin/darwin-arm64/pandoc

   # Sign bundled Node.js runtime
   codesign --force --timestamp --options runtime \
     --entitlements resources/entitlements.plist \
     --sign "Developer ID Application: Your Name (TEAM_ID)" \
     resources/mathjax/node
   ```

2. **Sign Embedded Frameworks & Shared Libraries:**
   ```bash
   find "dist/Polpo.app/Contents/Frameworks" -type f \( -name "*.dylib" -o -name "*.so" \) | while read -r lib; do
     codesign --force --timestamp --options runtime \
       --sign "Developer ID Application: Your Name (TEAM_ID)" "$lib"
   done
   ```

3. **Sign Main Application Bundle:**
   ```bash
   codesign --force --timestamp --options runtime \
     --entitlements resources/entitlements.plist \
     --sign "Developer ID Application: Your Name (TEAM_ID)" \
     "dist/Polpo.app"
   ```

4. **Verify Signature Integrity:**
   ```bash
   spctl --assess --type execute --verbose "dist/Polpo.app"
   codesign --verify --deep --strict --verbose=2 "dist/Polpo.app"
   ```

### 4.3 Apple Notarization & Stapling

```bash
# 1. Create distribution DMG or ZIP
ditto -c -k --keepParent "dist/Polpo.app" "dist/Polpo.zip"

# 2. Submit for Apple Notarization
xcrun notarytool submit "dist/Polpo.zip" \
  --keychain-profile "notary-profile" \
  --wait

# 3. Staple Notarization Ticket to Application
xcrun stapler staple "dist/Polpo.app"
```

---

## 5. Continuous Integration & Packaging Validation Matrix

Polpo exercises distinct automated workflows for continuous integration, release packaging, and periodic validation:
* **Linux PR/Push CI Runner (`ubuntu-24.04`):** Executes blocking quality gates (`scripts/run_quality_gates.py`), the primary test suite (`test-linux`), and Gitleaks secret scanning (`.github/workflows/ci.yml`).
* **Windows PR/Push CI Runner (`windows-2025`):** Executes the cross-platform test suite (`test-windows`), validating Win32 process creation flags (`CREATE_NO_WINDOW`), line endings (`\r\n`), and clean daemon termination on native Windows runners.
* **Linux Release Packaging Runner (`ubuntu-22.04`):** Packages standalone desktop bundles and executes the headless `--self-test` smoke test in `.github/workflows/release.yml`, ensuring that distributed binaries target the minimum `glibc >= 2.35` runtime baseline.
* **Periodic & Pre-Release Validation (`.github/workflows/periodic-validation.yml`):** Periodically runs test suites on macOS 15 ARM64 (Python 3.12) and across Python 3.10 and 3.11 on Linux.
* **Verification Script:** `scripts/verify_mathjax_worker.py` asserts protocol compliance, bounded memory buffers, and error isolation without test runner hangs.

---

## 6. Reproducible Desktop Package Building (`scripts/build_package.py`)

Standalone desktop packages are built using PyInstaller (`packaging/polpo.spec`, pinned to `pyinstaller==6.22.3`) and automated through `scripts/build_package.py`:

```bash
# 0. Install pinned PyInstaller build dependency
python -m pip install "pyinstaller==6.22.3"

# 1. Fetch pinned runtime binaries and production MathJax dependencies
python scripts/fetch_runtimes.py --install-mathjax-deps

# 2. Build standalone desktop bundle and deterministic archive
python scripts/build_package.py
```

### 6.1 Package Structure

The resulting directory bundle (`dist/Polpo/`) contains:
- The compiled `Polpo` executable (`Polpo.exe` on Windows).
- Hermetic runtime binaries: Pandoc 3.1.11 (`resources/bin/<platform>/`) and Node.js 22.23.2 (`resources/mathjax/node`).
- Bundled `mathjax_worker.js` with production `node_modules` (`mathjax-full@3.2.2`).
- Embedded SQLite schema migrations (`infrastructure/persistence/sqlite/migrations/`, migrations `001` through `005`).
- Curated prompt templates (`prompts/templates/`).
- Bundled LaTeX preamble template (`resources/templates/polpo_preamble.tex`).
- QML source components (`interfaces/desktop/qml/`).
- License file (`LICENSE` under AGPL-3.0-only).

### 6.2 Deterministic Archive Generation

`scripts/build_package.py` creates bit-for-bit reproducible release archives:
- **Linux:** `dist/Polpo-v<version>-linux-x86_64.tar.gz` (with normalized tar entries and fixed Gzip header timestamp).
- **Windows:** `dist/Polpo-v<version>-windows-x64.zip` (with normalized DOS timestamps).
- Accompanying SHA-256 checksum sidecars: `dist/Polpo-v<version>-<platform>.tar.gz.sha256`.

### 6.3 Frozen Binary `--self-test` Smoke Test

Before distribution, packaged binaries must be validated in an isolated directory outside the source tree:

```bash
# Extract archive to a clean temporary location
mkdir -p /tmp/polpo_smoke && tar -xzf dist/Polpo-*.tar.gz -C /tmp/polpo_smoke

# Execute self-test with constrained PATH (confirming bundled pandoc and node are used)
PATH="/usr/bin:/bin" QT_QPA_PLATFORM=offscreen /tmp/polpo_smoke/Polpo/Polpo --self-test
```

The `--self-test` flag exercises:
1. Frozen runtime path resolution (`sys._MEIPASS` / bundle root).
2. SQLite database migration application (exercises migrations `001` through `005` via container initialization, verifying all 9 schema tables).
3. Idempotent default prompt seeding into the clean SQLite database.
4. QML application engine initialization (`Main.qml`).
5. Bundled Pandoc 3.1.11 binary invocation (AST parsing with source positions).
6. Bundled Node.js 22.23.2 MathJax daemon IPC handshake and mathematical SVG vector rendering (`$E=mc^2$`).
7. Clean, zero-orphan process shutdown.

---

## 7. Release Pipeline and Provenance (`.github/workflows/release.yml`)

> [!NOTE]
> **Active Release Freeze & Candidate Status:** The project maintainer strictly enforces an active release freeze. Pre-release candidate `v0.1.0-rc.1` is currently published on GitHub Releases for preview testing, but no official stable release has been published yet. Creating, moving, drafting, or publishing new tags or release assets is frozen pending authorization.

Official releases and pre-release validation runs are orchestrated through a 4-job GitHub Actions workflow:

### 7.1 Triggers and Execution Modes
- **`push` to `refs/tags/v*`:** Canonical release trigger. Verifies ref ancestry on `main`, runs Tier-2 validation matrix, builds Linux/Windows packages, runs smoke tests, and publishes the release with cryptographic provenance.
- **`push` to `release-validation/**` branches:** CI validation trigger for pre-release testing. Builds packages and runs smoke tests across Linux and Windows without publishing.
- **`workflow_dispatch`:** Manual execution on any branch or ref for build and smoke-test verification. Publication is unreachable on manual dispatch.

### 7.2 Job DAG and Gating
1. **`validate-release-ref`:** Verifies tag format matches `core/version.py`, checks commit is on `origin/main`, runs Gitleaks secret scanning, and passes all repository quality gates (`scripts/run_quality_gates.py`).
2. **`tier2-validation-matrix`:** Executes the full test suite across macOS 15 ARM64 and Linux Python 3.10 and 3.11.
3. **`package-and-smoke-test`:** Builds standalone packages on Linux and Windows runners, unpacks them in clean temp directories outside the checkout, and executes `--self-test` with stripped system PATH.
4. **`publish-release`:** Strictly requires tag push (`startsWith(github.ref, 'refs/tags/v')`). Combines and verifies `SHA256SUMS.txt`, generates cryptographic build provenance attestations via `actions/attest-build-provenance`, and publishes the atomic GitHub Release with release notes.
