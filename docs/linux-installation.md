# Polpo Linux Installation and Update Guide

This document defines the universal Linux installation, update, dependency resolution, and uninstallation experience for Polpo Desktop.

---

## 1. Quick Start (Single-Command Install)

Polpo provides a single-command user-space installer that runs entirely without root permissions (`sudo` is never required for application installation).

> [!NOTE]
> **Active Release Freeze & Candidate Availability:** Official stable releases are not yet published, and an active release freeze is enforced by the maintainers. Only pre-release candidates (such as `v0.1.0-rc.1`) are currently available on GitHub Releases. Because the installer defaults to `--channel stable`, installing without flags queries GitHub for the latest stable release and reports that no published stable release was found. To install and evaluate the pre-release candidate, pass `--channel preview` or install directly from a downloaded archive with `--archive <path>`.

### Preview Channel (Pre-Release Candidates)

To install the latest pre-release candidate build (such as `v0.1.0-rc.1`):

```bash
curl -fsSL https://raw.githubusercontent.com/amirreza-a2a/Polpo/main/install.sh | bash -s -- --channel preview
```

### Stable Channel (Unavailable During Pre-Release Freeze)

Once the first official stable release is published, the single-command install command will be:

```bash
curl -fsSL https://raw.githubusercontent.com/amirreza-a2a/Polpo/main/install.sh | bash
```

*(Note: During the active release freeze, running this default command exits with an error reporting that no published stable release was found, directing users to the preview channel).*

### Offline / Local Archive Install

To install from a local release archive (e.g. downloaded workflow artifacts or pre-release tarball):

```bash
bash install.sh --archive Polpo-v0.1.0-linux-x86_64.tar.gz
```

---

## 2. Inspect-Before-Run Workflow

For security-conscious environments and air-gapped workstations, inspect the installation script before executing it:

```bash
# 1. Download installer script
curl -fsSL -o install.sh https://raw.githubusercontent.com/amirreza-a2a/Polpo/main/install.sh

# 2. Inspect script contents
less install.sh

# 3. Execute with desired options (e.g. preview channel or local archive)
bash install.sh --channel preview
```

---

## 3. Supported Distributions & System Requirements

### Architecture & Libc Requirements

| Requirement | Specification | Enforcement |
|---|---|---|
| **CPU Architecture** | `x86_64` (AMD64) | Non-x86_64 architectures (ARM64, i686) are rejected with exit code `2`. |
| **C Library** | GNU C Library (`glibc` >= 2.35) | Musl libc (Alpine Linux) is rejected with exit code `2`. |
| **Privileges** | Per-user (no root required) | Installed into `~/.local/opt/polpo` and `~/.local/bin`. |

### Automated CI Testing vs. Mapped Distributions

Polpo distinguishes between distributions verified in automated CI and distributions mapped for system dependencies:

* **Automatically Tested (CI Baseline):**
  * **Ubuntu 24.04 LTS (`glibc 2.39`):** Continuous integration matrix runner for blocking pull request quality gates, unit and integration test suites, and secret scanning (`.github/workflows/ci.yml`).
  * **Ubuntu 22.04 LTS (`glibc 2.35`):** Release packaging build runner and automated frozen binary smoke-test environment in GitHub Actions (`.github/workflows/release.yml`), establishing the `glibc >= 2.35` runtime baseline for distributed archives.
* **Mapped Distributions (Tier 3 Best-Effort):**
  * **Debian 12 Bookworm+**, **Fedora 39+**, **Arch Linux**, and **openSUSE Leap/Tumbleweed** have system package manager mappings defined in `polpo-deps` (`scripts/polpo_linux_deps.sh`). However, compatibility is not proven by continuous CI on those distributions; execution depends on local display drivers, Wayland/X11 configuration, and shared library availability.
* **Unsupported Systems (Tier 4):**
  * **Non-x86_64 Architectures:** ARM64, i686, and 32-bit platforms are unsupported and rejected at preflight.
  * **Musl-based Distributions:** Alpine Linux and other musl libc environments are unsupported and rejected at preflight.

---

## 4. Distro GUI Dependencies

Polpo is a native PySide6 / QtQuick application. While the standalone application bundle packages Python, Qt/PySide6 runtime components, Pandoc 3.1.11, and Node.js 22.23.2 with MathJax 3.2.2, your Linux desktop display environment requires underlying system graphics and D-Bus libraries.

Polpo bundles a dependency helper: `polpo-deps` (also available via `scripts/polpo_linux_deps.sh`).

### Check Missing GUI Dependencies

```bash
polpo-deps check
```

### Automatic GUI Dependency Installation

To install missing libraries with your native package manager (prompts for `sudo` consent):

```bash
polpo-deps install-gui
```

To review commands without making changes:

```bash
polpo-deps install-gui --dry-run
```

### Manual Package Installation by Distribution

If you prefer installing dependencies manually with your system package manager:

* **Debian / Ubuntu:**
  ```bash
  sudo apt-get update && sudo apt-get install -y libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3
  ```
* **Fedora:**
  ```bash
  sudo dnf install -y mesa-libGL mesa-libEGL libxkbcommon-x11 dbus-libs
  ```
* **Arch Linux:**
  ```bash
  sudo pacman -S --needed libglvnd libxkbcommon-x11 dbus
  ```
* **openSUSE:**
  ```bash
  sudo zypper install -y libGL1 libEGL1 libxkbcommon-x11-0 libdbus-1-3
  ```

---

## 5. Optional PDF Export Dependencies

Polpo features an integrated PDF publication pipeline capable of compiling Markdown documents with bilingual Persian/English typography, TikZ vector diagrams, and mathematical formulas.

### Check PDF Toolchain Status

```bash
polpo-deps pdf
```

This diagnostic inspects the presence and versions of:
* **Quarto CLI** (`quarto`) — Document orchestration engine.
* **XeLaTeX** (`xelatex`) — High-fidelity typesetting engine for bidirectional and Persian fonts.
* **Poppler Utilities** (`pdftoppm`) — Vector crop extraction from PDF pages.
* **Typst** (`typst`) — Modern fast document compiler.
* **WeasyPrint** (`weasyprint`) — HTML/CSS to PDF renderer.

### Install PDF Export Toolchain

```bash
polpo-deps install-pdf
```

To preview required packages without installing:

```bash
polpo-deps install-pdf --dry-run
```

---

## 6. Installed Commands and File Locations

The installer sets up a clean, standard per-user filesystem structure conforming to XDG standards:

```text
~/.local/
├── bin/
│   ├── polpo              # Main application launcher
│   ├── polpo-update       # In-place release updater
│   ├── polpo-deps         # Distro dependency helper
│   └── polpo-uninstall    # Application uninstaller
├── opt/
│   └── polpo/
│       ├── current -> versions/v0.1.0/     # Active version symlink
│       ├── previous -> versions/v0.0.9/    # Rollback version symlink
│       └── versions/
│           ├── v0.1.0/                     # Packaged distribution files
│           └── ...
└── share/
    ├── applications/
    │   └── polpo.desktop                   # Desktop application menu entry
    └── polpot/                             # USER STATE (DATABASES & CREDENTIALS)
        ├── polpot.db                       # SQLite database (NEVER DELETED)
        └── ...
```

### Available CLI Commands

* `polpo` — Launch the Polpo desktop application.
* `polpo --self-test` — Run headless sanity check outside graphical environment.
* `polpo-update` — Check for updates and update in place.
* `polpo-deps [check|gui|pdf|install-gui|install-pdf]` — Diagnose and resolve system dependencies.
* `polpo-uninstall` — Remove application binaries, symlinks, and desktop menu entry.

Make sure `~/.local/bin` is in your shell `PATH`. If it is not already present, add it to your `~/.bashrc` or `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## 7. Updates & Version Lifecycle

### Updating Polpo

During the active release freeze, official stable releases have not yet been published. Running `polpo-update` without arguments queries the stable channel, reports that no published stable release was found, and guides you to the preview channel.

To check for and upgrade to pre-release candidate builds (such as `v0.1.0-rc.1`):

```bash
polpo-update --channel preview
```

Once the first official stable release is published, running `polpo-update` without flags will automatically check and upgrade on the stable channel:

```bash
polpo-update
```

### Atomic Updates & Safe Rollback

1. **Staged Extraction:** New versions are downloaded and extracted into a clean directory under `~/.local/opt/polpo/versions/<new-version>`.
2. **Integrity Preflight:** The installer verifies that the binary exists, is executable, and passes `--self-test` before modifying any links.
3. **Atomic Switch:** `~/.local/opt/polpo/current` is atomically updated using `ln -sfn`.
4. **Automatic Rollback:** If extraction or verification fails, the installer restores `current` to `previous` and aborts cleanly.
5. **Bounded Retention:** Up to 3 historical versions are preserved; older versions are automatically pruned. The active `current` and fallback `previous` versions are never pruned.

---

## 8. Integrity & Security Verification

All downloads are secured through multiple validation layers:

1. **SHA-256 Sidecar Verification:** For every archive downloaded (e.g. `polpo-linux-x86_64.tar.gz`), the installer downloads the matching `.sha256` sidecar from the official GitHub Release and verifies the archive hash with `sha256sum -c` before extraction.
2. **Archive Path Hardening:** The installer inspects tarball headers to reject any archive containing absolute paths (`/`) or directory traversal components (`..`).
3. **Zero Root Privilege Requirement:** The application never requests root permissions to install or run. Root (`sudo`) is only requested if you explicitly invoke `polpo-deps install-gui` or `polpo-deps install-pdf`.
4. **Data Isolation:** User settings, SQLite databases, and credentials in `~/.local/share/polpot` are strictly isolated from the application installation directory (`~/.local/opt/polpo`).

---

## 9. Uninstallation

To remove Polpo from your system:

```bash
polpo-uninstall
```

### What Gets Removed
* Application directory: `~/.local/opt/polpo`
* CLI wrappers: `~/.local/bin/polpo`, `polpo-update`, `polpo-deps`, `polpo-uninstall`
* Desktop launcher: `~/.local/share/applications/polpo.desktop`

### What Is Strictly Preserved
* Your database, transcription history, document drafts, and encrypted credentials in `~/.local/share/polpot` are **never touched or deleted**.
