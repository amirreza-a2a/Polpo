# PolpoT Packaging and Cross-Platform Distribution Guide

This document defines the packaging, runtime bundling, and code-signing architecture for PolpoT Desktop across Linux, Windows, and macOS target environments (per AGENTS.md Rule 28 and Phase 12 Architecture Wayfinder).

---

## 1. Runtime Bundling Architecture

PolpoT is a desktop-first, local-first, serverless application. All parsing (Pandoc) and math vector rendering (MathJax via headless Node.js daemon) execute entirely locally on the user's workstation without remote backend servers or cloud APIs.

To ensure hermetic offline execution without requiring end-users to manually install Pandoc or Node.js, PolpoT packages standalone binaries within the application bundle:

```text
polpot/
├── resources/
│   ├── runtimes.json                # Upstream acquisition manifest with SHA-256 hashes
│   ├── bin/
│   │   ├── linux-x64/pandoc         # Statically linked musl binary (v3.1.11)
│   │   ├── win-x64/pandoc.exe       # Standalone Windows executable (v3.1.11)
│   │   └── macos-universal/pandoc   # Universal binary (v3.1.11)
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
* **Static Linkage:** The bundled Linux Pandoc binary is statically linked against musl libc, eliminating runtime dependencies on glibc versions across distributions (Ubuntu, Fedora, Arch, Alpine).
* **System GUI Dependencies:** PySide6 requires standard desktop graphic libraries: `libgl1`, `libegl1`, `libxkbcommon-x11-0`, and `libdbus-1-3`.

### 3.2 Windows (Win32 / NTFS)

* **File Extensions & Path Separators:** Binaries must include the `.exe` extension (`pandoc.exe`, `node.exe`). Paths must be manipulated using `pathlib.Path` to preserve platform-appropriate backslash separators (`\`).
* **Hidden Console Windows:** Background worker subprocesses (`PandocRunner` and `MathJaxProcessSupervisor`) must pass `creationflags=subprocess.CREATE_NO_WINDOW` (`0x08000000`) on Windows to suppress flickering console windows when launching helper binaries.
* **Process Termination:** Windows lacks POSIX `SIGTERM`. `MathJaxProcessSupervisor` uses line-delimited `{"method": "shutdown"}` over stdin followed by `proc.terminate()` and `proc.kill()` for cooperative teardown without orphaned processes.

---

## 4. macOS Developer ID Code Signing & Notarization

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
     resources/bin/macos-universal/pandoc

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

## 5. Continuous Integration Validation Matrix

The repository CI workflow (`.github/workflows/ci.yml`) validates runtime contracts on every pull request and commit:
* **Linux (`ubuntu-24.04`):** Validates Pandoc execution, Node 22.23.2 MathJax daemon startup, rendering, and clean shutdown under POSIX process semantics.
* **Windows (`windows-2025`):** Validates Pandoc execution, Win32 process creation flags (`CREATE_NO_WINDOW`), line endings (`\r\n`), and clean daemon termination on native Windows runners.
* **Verification Script:** `scripts/verify_mathjax_worker.py` asserts protocol compliance, bounded memory buffers, and error isolation without test runner hangs.
