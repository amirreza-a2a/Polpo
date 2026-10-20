#!/usr/bin/env bash
# ==============================================================================
# Polpo Universal Linux Installer & Updater
#
# Production-grade user-space installer and updater for amirreza-a2a/Polpo.
#
# Key Features:
# - Installs per-user into ~/.local/opt/polpo and ~/.local/bin without root/sudo
# - Verifies architecture (x86_64) and libc (glibc; rejects musl/Alpine)
# - Validates SHA-256 checksums and tarball layout before extraction
# - Atomic version switching and safe rollback preservation
# - Bounded version retention (never removes active or previous rollback version)
# - Preserves all user data (~/.local/share/polpot, databases, artifacts, settings)
# - Distribution-aware dependency detection via polpo-deps
# ==============================================================================

set -euo pipefail

MAIN_SCRIPT_SOURCE="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"

# Configuration defaults
DEFAULT_REPO="amirreza-a2a/Polpo"
REPO="${POLPO_REPO:-$DEFAULT_REPO}"
INSTALL_DIR="${POLPO_INSTALL_DIR:-$HOME/.local/opt/polpo}"
BIN_DIR="${POLPO_BIN_DIR:-$HOME/.local/bin}"
CHANNEL="stable"
REQUESTED_VERSION=""
LOCAL_ARCHIVE=""
UPDATE_MODE=false
ROLLBACK_MODE=false
UNINSTALL_MODE=false
FORCE_MODE=false
DRY_RUN=false

print_usage() {
    cat << 'EOF'
Polpo Linux Installer and Updater

Usage:
  install.sh [OPTIONS]
  polpo-update [OPTIONS]
  polpo-uninstall [OPTIONS]

Options:
  --channel <stable|preview>  Release channel (default: stable)
  -v, --version <version>     Install a specific release version (e.g. 0.1.0 or v0.1.0-rc.1)
  --archive <file.tar.gz>     Install from a local distribution archive (offline mode)
  --dir <path>                Installation target directory (default: ~/.local/opt/polpo)
  --bin-dir <path>            Binary symlink directory (default: ~/.local/bin)
  --update                    Check for and install the latest version on the chosen channel
  --rollback                  Revert to the previous installed version
  --uninstall                 Remove installed application files, symlinks, and desktop entry
  --force                     Force reinstallation even if the same version is installed
  --dry-run                   Simulate steps without modifying files or directories
  --repo <owner/repo>         GitHub repository slug (default: amirreza-a2a/Polpo)
  -h, --help                  Show this help message
EOF
}

# ------------------------------------------------------------------------------
# Logging Helpers
# ------------------------------------------------------------------------------

log_info() {
    echo ":: [polpo] $*"
}

log_warn() {
    echo ":: [WARN] $*" >&2
}

log_error() {
    echo ":: [ERROR] $*" >&2
}

# ------------------------------------------------------------------------------
# Preflight Validation
# ------------------------------------------------------------------------------

check_privileges() {
    if [[ "$(id -u)" -eq 0 ]]; then
        log_error "Polpo installer must not be run as root (or with sudo)."
        log_error "Polpo installs safely per-user in ~/.local/opt/polpo and ~/.local/bin."
        log_error "System packages (if needed) can be installed via 'polpo-deps install-gui'."
        return 1
    fi
}

check_system_compatibility() {
    # Check OS
    local os_type
    os_type="$(uname -s)"
    if [[ "$os_type" != "Linux" ]]; then
        log_error "This installer only supports Linux. Detected OS: ${os_type}"
        return 2
    fi

    # Check Architecture
    local arch
    arch="$(uname -m)"
    if [[ "$arch" != "x86_64" && "$arch" != "amd64" ]]; then
        log_error "Polpo currently only provides official standalone binaries for x86_64 architecture."
        log_error "Detected architecture: ${arch}"
        return 2
    fi

    # Check Libc (Reject musl/Alpine)
    if [[ -f /etc/alpine-release ]] || (command -v ldd >/dev/null 2>&1 && ldd /bin/ls 2>&1 | grep -iq "musl"); then
        log_error "Polpo requires glibc and does not support musl-based distributions (e.g. Alpine Linux)."
        return 2
    fi
}

check_required_tools() {
    local missing_tools=()
    for tool in tar gzip sha256sum; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            missing_tools+=("$tool")
        fi
    done

    # Need at least curl or wget for remote downloads
    if [[ -z "$LOCAL_ARCHIVE" ]]; then
        if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
            missing_tools+=("curl or wget")
        fi
    fi

    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required system utilities: ${missing_tools[*]}"
        log_error "Please install the missing utilities using your distribution package manager."
        return 1
    fi
}

download_file() {
    local url="$1"
    local dest="$2"

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$dest" "$url"
    else
        log_error "Neither curl nor wget is available for downloading."
        return 1
    fi
}

# ------------------------------------------------------------------------------
# Concurrency Locking
# ------------------------------------------------------------------------------

acquire_install_lock() {
    local target_dir="$1"
    local lock_hash
    lock_hash="$(echo -n "$target_dir" | sha256sum | awk '{print $1}' | cut -c1-16)"
    LOCK_DIR="/tmp/polpo-install-$(id -u)-${lock_hash}.lock.d"

    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        local pid_file="$LOCK_DIR/pid"
        if [[ -f "$pid_file" ]]; then
            local active_pid
            active_pid="$(cat "$pid_file" 2>/dev/null || true)"
            if [[ -n "$active_pid" ]] && ! kill -0 "$active_pid" 2>/dev/null; then
                log_warn "Cleaning up stale installation lock from dead process ${active_pid}..."
                rm -rf "$LOCK_DIR"
                mkdir "$LOCK_DIR" 2>/dev/null || {
                    log_error "Failed to acquire installation lock at ${LOCK_DIR}"
                    return 1
                }
            else
                log_error "Another Polpo installation or update process is currently running (PID ${active_pid})."
                return 1
            fi
        else
            log_error "Another Polpo installation or update process is currently running."
            return 1
        fi
    fi

    echo "$$" > "$LOCK_DIR/pid"
    # Register cleanup trap
    trap 'rm -rf "'"$LOCK_DIR"'"' EXIT INT TERM
}

release_install_lock() {
    if [[ -n "${LOCK_DIR:-}" && -d "${LOCK_DIR:-}" ]]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
    trap - EXIT INT TERM
}

# ------------------------------------------------------------------------------
# Archive Verification & Extraction
# ------------------------------------------------------------------------------

verify_checksum() {
    local archive_path="$1"
    local checksum_path="$2"

    log_info "Verifying SHA-256 checksum for $(basename "$archive_path")..."
    local expected_hash
    expected_hash="$(awk '{print $1}' "$checksum_path" | head -n1 | tr '[:upper:]' '[:lower:]')"
    if [[ -z "$expected_hash" || ${#expected_hash} -ne 64 ]]; then
        log_error "Invalid or malformed checksum file: ${checksum_path}"
        return 1
    fi

    local actual_hash
    actual_hash="$(sha256sum "$archive_path" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
    if [[ "$actual_hash" != "$expected_hash" ]]; then
        log_error "SHA-256 checksum mismatch for ${archive_path}!"
        log_error "  Expected: ${expected_hash}"
        log_error "  Actual  : ${actual_hash}"
        return 1
    fi
    log_info "SHA-256 verification passed."
}

validate_archive_layout() {
    local archive_path="$1"
    log_info "Validating archive structure and path safety..."

    local unsafe_entries=()
    local top_entries=()

    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue

        # Check for absolute paths
        if [[ "$entry" == /* ]]; then
            log_error "Unsafe archive: contains absolute path '${entry}'"
            return 1
        fi

        # Check for path traversal (..)
        if [[ "$entry" =~ (^|/)\.\.(/|$) ]]; then
            log_error "Unsafe archive: contains directory traversal '${entry}'"
            return 1
        fi

        # Track top-level prefix
        local root_segment
        root_segment="${entry%%/*}"
        if [[ "$root_segment" != "Polpo" ]]; then
            log_error "Archive contains unexpected top-level entry: '${entry}' (expected root 'Polpo/')"
            return 1
        fi
    done < <(tar -ztf "$archive_path")

    log_info "Archive layout validated safely."
}

verify_extracted_bundle() {
    local bundle_dir="$1"
    log_info "Verifying application bundle integrity in ${bundle_dir}..."

    local exe="${bundle_dir}/Polpo"
    if [[ ! -f "$exe" ]]; then
        log_error "Application binary missing at expected path: ${exe}"
        return 1
    fi

    if [[ ! -x "$exe" ]]; then
        chmod +x "$exe" 2>/dev/null || true
        if [[ ! -x "$exe" ]]; then
            log_error "Application binary is not executable: ${exe}"
            return 1
        fi
    fi

    # Verify critical resource directories
    if [[ ! -d "${bundle_dir}/resources" && ! -d "${bundle_dir}/_internal" ]]; then
        log_warn "Application bundle does not appear to contain standard resources directory."
    fi

    log_info "Bundle integrity verified."
}

# ------------------------------------------------------------------------------
# Release Resolution
# ------------------------------------------------------------------------------

resolve_stable_release() {
    log_info "Resolving latest stable Polpo release from ${REPO}..."
    local api_url="https://api.github.com/repos/${REPO}/releases/latest"

    local http_code=""
    if command -v curl >/dev/null 2>&1; then
        http_code="$(curl -sI -o /dev/null -w "%{http_code}" "$api_url" || echo "000")"
    elif command -v wget >/dev/null 2>&1; then
        http_code="$(wget --spider -S "$api_url" 2>&1 | awk '/HTTP\// {print $2}' | tail -n1 || echo "000")"
    fi

    if [[ "$http_code" == "404" || "$http_code" == "000" ]]; then
        log_error "No published stable release was found for ${REPO}."
        log_error "Polpo has not published an official stable release yet."
        echo ""
        log_info "To install the latest pre-release, use the preview channel:"
        log_info "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash -s -- --channel preview"
        echo ""
        log_info "Or install directly from a local archive:"
        log_info "  install.sh --archive /path/to/Polpo-v0.1.0-linux-x86_64.tar.gz"
        return 1
    fi

    # Resolve direct download URL
    RESOLVED_ARCHIVE_URL="https://github.com/${REPO}/releases/latest/download/polpo-linux-x86_64.tar.gz"
    RESOLVED_CHECKSUM_URL="https://github.com/${REPO}/releases/latest/download/polpo-linux-x86_64.tar.gz.sha256"
    RESOLVED_VERSION="latest"
}

resolve_preview_or_tag_release() {
    local target_tag="$1"
    log_info "Resolving release metadata from ${REPO} (tag/preview: ${target_tag:-preview})..."
    local api_url="https://api.github.com/repos/${REPO}/releases"
    if [[ -n "$target_tag" ]]; then
        api_url="https://api.github.com/repos/${REPO}/releases/tags/${target_tag}"
    fi

    local temp_json
    temp_json="$(mktemp)"
    if ! download_file "$api_url" "$temp_json"; then
        log_error "Failed to retrieve release metadata from ${api_url}."
        rm -f "$temp_json"
        return 1
    fi

    # Extract download URL and tag name safely using python
    local res
    res="$(python3 -c '
import json, sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)

    release = data[0] if isinstance(data, list) else data
    tag = release.get("tag_name", "")

    tar_url = ""
    sha_url = ""
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith("linux-x86_64.tar.gz"):
            tar_url = asset.get("browser_download_url", "")
        elif name.endswith("linux-x86_64.tar.gz.sha256"):
            sha_url = asset.get("browser_download_url", "")

    if not tar_url:
        sys.exit(2)
    print(f"{tag}\t{tar_url}\t{sha_url}")
except Exception as e:
    sys.exit(1)
' "$temp_json" || true)"
    rm -f "$temp_json"

    if [[ -z "$res" ]]; then
        log_error "No Linux x86_64 distribution asset found in release metadata."
        return 1
    fi

    RESOLVED_VERSION="$(echo "$res" | cut -f1)"
    RESOLVED_ARCHIVE_URL="$(echo "$res" | cut -f2)"
    RESOLVED_CHECKSUM_URL="$(echo "$res" | cut -f3)"
    log_info "Found release: ${RESOLVED_VERSION}"
}

# ------------------------------------------------------------------------------
# Installation & Atomic Switch
# ------------------------------------------------------------------------------

install_bundle() {
    local staging_bundle="$1"
    local version_id="$2"

    mkdir -p "${INSTALL_DIR}/versions"
    mkdir -p "${INSTALL_DIR}/bin"
    mkdir -p "${BIN_DIR}"

    local target_version_dir="${INSTALL_DIR}/versions/${version_id}"

    # Handle same-version reinstallation
    if [[ -d "$target_version_dir" ]]; then
        if [[ "$FORCE_MODE" != "true" && "$UPDATE_MODE" == "true" ]]; then
            log_info "Polpo is already up to date (version ${version_id})."
            return 0
        fi
        log_info "Replacing existing installation directory for version ${version_id}..."
        rm -rf "${target_version_dir}.old" 2>/dev/null || true
        mv "$target_version_dir" "${target_version_dir}.old"
    fi

    # Move staged bundle to version directory
    mv "$staging_bundle" "$target_version_dir"
    rm -rf "${target_version_dir}.old" 2>/dev/null || true

    # Record version metadata
    echo "$version_id" > "${target_version_dir}/.polpo_version"

    # Copy installer and helper scripts into installation bin directory
    if [[ -f "${MAIN_SCRIPT_SOURCE:-}" ]]; then
        cp "${MAIN_SCRIPT_SOURCE}" "${INSTALL_DIR}/bin/install.sh"
        chmod +x "${INSTALL_DIR}/bin/install.sh"
    fi

    # Bundle/install polpo_linux_deps.sh
    local deps_source=""
    if [[ -f "${target_version_dir}/scripts/polpo_linux_deps.sh" ]]; then
        deps_source="${target_version_dir}/scripts/polpo_linux_deps.sh"
    elif [[ -f "$(dirname "${MAIN_SCRIPT_SOURCE:-$0}")/scripts/polpo_linux_deps.sh" ]]; then
        deps_source="$(dirname "${MAIN_SCRIPT_SOURCE:-$0}")/scripts/polpo_linux_deps.sh"
    fi

    if [[ -n "$deps_source" && -f "$deps_source" ]]; then
        cp "$deps_source" "${INSTALL_DIR}/bin/polpo_linux_deps.sh"
        chmod +x "${INSTALL_DIR}/bin/polpo_linux_deps.sh"
    fi

    # Create polpo-update wrapper
    cat << 'EOF' > "${INSTALL_DIR}/bin/polpo-update"
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/install.sh" --update "$@"
EOF
    chmod +x "${INSTALL_DIR}/bin/polpo-update"

    # Create polpo-deps wrapper
    cat << 'EOF' > "${INSTALL_DIR}/bin/polpo-deps"
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/polpo_linux_deps.sh" ]]; then
    exec "${SCRIPT_DIR}/polpo_linux_deps.sh" "$@"
elif [[ -f "${SCRIPT_DIR}/../current/scripts/polpo_linux_deps.sh" ]]; then
    exec "${SCRIPT_DIR}/../current/scripts/polpo_linux_deps.sh" "$@"
else
    echo "Error: polpo_linux_deps.sh not found." >&2
    exit 1
fi
EOF
    chmod +x "${INSTALL_DIR}/bin/polpo-deps"

    # Create polpo-uninstall wrapper
    cat << 'EOF' > "${INSTALL_DIR}/bin/polpo-uninstall"
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/install.sh" --uninstall "$@"
EOF
    chmod +x "${INSTALL_DIR}/bin/polpo-uninstall"

    # Update rollback previous pointer
    if [[ -L "${INSTALL_DIR}/current" ]]; then
        local prev_target
        prev_target="$(readlink "${INSTALL_DIR}/current" || true)"
        if [[ -n "$prev_target" && "$prev_target" != "versions/${version_id}" ]]; then
            ln -sfn "$prev_target" "${INSTALL_DIR}/previous"
        fi
    fi

    # Atomic switch of 'current' symlink
    log_info "Atomically switching active Polpo version to ${version_id}..."
    ln -sfn "versions/${version_id}" "${INSTALL_DIR}/current.tmp"
    mv -Tf "${INSTALL_DIR}/current.tmp" "${INSTALL_DIR}/current" 2>/dev/null || {
        # Fallback if mv -T is unsupported
        rm -f "${INSTALL_DIR}/current"
        ln -s "versions/${version_id}" "${INSTALL_DIR}/current"
    }

    # Symlink binaries into user bin directory
    ln -sf "${INSTALL_DIR}/current/Polpo" "${BIN_DIR}/polpo"
    ln -sf "${INSTALL_DIR}/bin/polpo-update" "${BIN_DIR}/polpo-update"
    ln -sf "${INSTALL_DIR}/bin/polpo-deps" "${BIN_DIR}/polpo-deps"
    ln -sf "${INSTALL_DIR}/bin/polpo-uninstall" "${BIN_DIR}/polpo-uninstall"

    # Setup Desktop entry if applicable
    install_desktop_entry "${version_id}"

    # Prune old versions (retain bounded count, never prune current or previous)
    prune_historical_versions

    # Check if Polpo is currently running and advise
    check_running_process

    log_info "Polpo ${version_id} successfully installed!"
    print_path_notice
}

install_desktop_entry() {
    local version_id="$1"
    local app_dir="${HOME}/.local/share/applications"
    mkdir -p "$app_dir"

    local desktop_file="${app_dir}/polpo.desktop"
    local exe_path="${BIN_DIR}/polpo"

    # Look for icon in bundle
    local icon_line=""
    for icon_candidate in "${INSTALL_DIR}/current/resources/icon.png" "${INSTALL_DIR}/current/resources/polpo.png"; do
        if [[ -f "$icon_candidate" ]]; then
            icon_line="Icon=${icon_candidate}"
            break
        fi
    done

    cat << EOF > "$desktop_file"
[Desktop Entry]
Type=Application
Name=Polpo
Comment=Desktop-first STEM document transcription and conversion
Exec=${exe_path}
Terminal=false
Categories=Office;Utility;
StartupNotify=true
${icon_line}
EOF
    chmod 644 "$desktop_file"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$app_dir" >/dev/null 2>&1 || true
    fi
}

prune_historical_versions() {
    local max_keep=3
    local versions_dir="${INSTALL_DIR}/versions"
    [[ ! -d "$versions_dir" ]] && return 0

    local current_target=""
    local previous_target=""
    [[ -L "${INSTALL_DIR}/current" ]] && current_target="$(basename "$(readlink "${INSTALL_DIR}/current")")"
    [[ -L "${INSTALL_DIR}/previous" ]] && previous_target="$(basename "$(readlink "${INSTALL_DIR}/previous")")"

    # List versions sorted by modification time (oldest first)
    local all_versions=()
    while IFS= read -r v; do
        [[ -n "$v" ]] && all_versions+=("$v")
    done < <(find "$versions_dir" -mindepth 1 -maxdepth 1 -type d -printf "%T@ %p\n" | sort -n | awk '{print $2}')

    local total=${#all_versions[@]}
    if (( total > max_keep )); then
        local to_remove=$(( total - max_keep ))
        for (( i=0; i<to_remove; i++ )); do
            local dir="${all_versions[$i]}"
            local bname="$(basename "$dir")"
            if [[ "$bname" != "$current_target" && "$bname" != "$previous_target" ]]; then
                log_info "Pruning old version: ${bname}"
                rm -rf "$dir"
            fi
        done
    fi
}

check_running_process() {
    if pgrep -x Polpo >/dev/null 2>&1; then
        log_info "Notice: Polpo is currently running."
        log_info "The new version has been staged and will be used on next launch."
    fi
}

print_path_notice() {
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo ""
        echo "=================================================================="
        echo "Notice: ${BIN_DIR} is not in your system PATH."
        echo "To run 'polpo', 'polpo-update', and 'polpo-deps' directly, add it"
        echo "to your shell configuration file:"
        echo ""
        echo "  For bash (in ~/.bashrc):"
        echo "    export PATH=\"${BIN_DIR}:\$PATH\""
        echo ""
        echo "  For zsh (in ~/.zshrc):"
        echo "    export PATH=\"${BIN_DIR}:\$PATH\""
        echo ""
        echo "  For fish (in ~/.config/fish/config.fish):"
        echo "    fish_add_path ${BIN_DIR}"
        echo "=================================================================="
        echo ""
    fi
}

# ------------------------------------------------------------------------------
# Rollback & Uninstall Logic
# ------------------------------------------------------------------------------

run_rollback() {
    if [[ ! -L "${INSTALL_DIR}/previous" ]]; then
        log_error "No previous Polpo version available for rollback in ${INSTALL_DIR}."
        return 1
    fi

    local prev_target
    prev_target="$(readlink "${INSTALL_DIR}/previous")"
    if [[ ! -d "${INSTALL_DIR}/${prev_target}" && ! -d "$prev_target" ]]; then
        log_error "Previous version target directory does not exist: ${prev_target}"
        return 1
    fi

    local current_target
    current_target="$(readlink "${INSTALL_DIR}/current")"

    log_info "Rolling back Polpo from ${current_target} to ${prev_target}..."
    ln -sfn "$prev_target" "${INSTALL_DIR}/current.tmp"
    mv -Tf "${INSTALL_DIR}/current.tmp" "${INSTALL_DIR}/current" 2>/dev/null || {
        rm -f "${INSTALL_DIR}/current"
        ln -s "$prev_target" "${INSTALL_DIR}/current"
    }

    # Update previous pointer to point to the version we rolled back from
    ln -sfn "$current_target" "${INSTALL_DIR}/previous"

    log_info "Rollback complete. Active version is now $(basename "$prev_target")."
    return 0
}

run_uninstall() {
    log_info "Uninstalling Polpo application..."

    # 1. Remove symlinks in BIN_DIR
    for sym in polpo polpo-update polpo-deps polpo-uninstall; do
        if [[ -L "${BIN_DIR}/${sym}" ]]; then
            log_info "Removing symlink: ${BIN_DIR}/${sym}"
            rm -f "${BIN_DIR}/${sym}"
        fi
    done

    # 2. Remove desktop file
    local desktop_file="${HOME}/.local/share/applications/polpo.desktop"
    if [[ -f "$desktop_file" ]]; then
        log_info "Removing desktop entry: ${desktop_file}"
        rm -f "$desktop_file"
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "${HOME}/.local/share/applications" >/dev/null 2>&1 || true
        fi
    fi

    # 3. Remove application installation directory
    if [[ -d "$INSTALL_DIR" ]]; then
        log_info "Removing application files at: ${INSTALL_DIR}"
        rm -rf "$INSTALL_DIR"
    fi

    echo ""
    log_info "Polpo application files and shortcuts have been successfully uninstalled."
    log_info "Safety check: User profile data, SQLite databases, and credentials"
    log_info "located at ~/.local/share/polpot were untouched and PRESERVED."
    return 0
}

# ------------------------------------------------------------------------------
# Main Flow
# ------------------------------------------------------------------------------

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --channel)
                [[ $# -lt 2 ]] && { log_error "--channel requires an argument"; return 1; }
                CHANNEL="$2"
                shift 2
                ;;
            -v|--version)
                [[ $# -lt 2 ]] && { log_error "--version requires an argument"; return 1; }
                REQUESTED_VERSION="$2"
                shift 2
                ;;
            --archive)
                [[ $# -lt 2 ]] && { log_error "--archive requires an argument"; return 1; }
                LOCAL_ARCHIVE="$2"
                shift 2
                ;;
            --dir)
                [[ $# -lt 2 ]] && { log_error "--dir requires an argument"; return 1; }
                INSTALL_DIR="$2"
                shift 2
                ;;
            --bin-dir)
                [[ $# -lt 2 ]] && { log_error "--bin-dir requires an argument"; return 1; }
                BIN_DIR="$2"
                shift 2
                ;;
            --repo)
                [[ $# -lt 2 ]] && { log_error "--repo requires an argument"; return 1; }
                REPO="$2"
                shift 2
                ;;
            --update)
                UPDATE_MODE=true
                shift
                ;;
            --rollback)
                ROLLBACK_MODE=true
                shift
                ;;
            --uninstall)
                UNINSTALL_MODE=true
                shift
                ;;
            --force)
                FORCE_MODE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            -h|--help)
                print_usage
                return 0
                ;;
            *)
                log_error "Unknown argument: $1"
                print_usage
                return 1
                ;;
        esac
    done

    check_privileges
    check_system_compatibility

    if [[ "$UNINSTALL_MODE" == "true" ]]; then
        run_uninstall
        return $?
    fi

    if [[ "$ROLLBACK_MODE" == "true" ]]; then
        run_rollback
        return $?
    fi

    check_required_tools

    acquire_install_lock "$INSTALL_DIR"

    local temp_dir
    temp_dir="$(mktemp -d)"
    # Ensure temp cleanup on unexpected exit
    local outer_cleanup="rm -rf '${temp_dir}'"

    local archive_file=""
    local checksum_file=""
    local version_id="0.1.0"

    if [[ -n "$LOCAL_ARCHIVE" ]]; then
        # Offline or direct archive installation
        if [[ ! -f "$LOCAL_ARCHIVE" ]]; then
            log_error "Specified archive file not found: ${LOCAL_ARCHIVE}"
            rm -rf "$temp_dir"
            return 1
        fi
        archive_file="$LOCAL_ARCHIVE"
        local sidecar="${LOCAL_ARCHIVE}.sha256"
        if [[ -f "$sidecar" ]]; then
            checksum_file="$sidecar"
        fi

        # Infer version from filename (e.g. Polpo-v0.1.0-linux-x86_64.tar.gz -> 0.1.0)
        local base_name
        base_name="$(basename "$LOCAL_ARCHIVE")"
        local clean_name="${base_name%-linux-x86_64.tar.gz}"
        clean_name="${clean_name%.tar.gz}"
        clean_name="${clean_name#Polpo-}"
        clean_name="${clean_name#polpo-}"
        clean_name="${clean_name#v}"
        if [[ "$clean_name" =~ ^([0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?)$ ]]; then
            version_id="${BASH_REMATCH[1]}"
        elif [[ "$base_name" =~ (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.]+)? ]]; then
            version_id="${BASH_REMATCH[0]}"
        elif [[ -n "$REQUESTED_VERSION" ]]; then
            version_id="${REQUESTED_VERSION#v}"
        fi
    else
        # Remote download from GitHub
        if [[ -n "$REQUESTED_VERSION" ]]; then
            resolve_preview_or_tag_release "$REQUESTED_VERSION"
        elif [[ "$CHANNEL" == "preview" ]]; then
            resolve_preview_or_tag_release ""
        else
            resolve_stable_release
        fi

        version_id="${RESOLVED_VERSION#v}"
        archive_file="${temp_dir}/polpo.tar.gz"
        checksum_file="${temp_dir}/polpo.tar.gz.sha256"

        log_info "Downloading ${RESOLVED_ARCHIVE_URL}..."
        download_file "$RESOLVED_ARCHIVE_URL" "$archive_file"

        if [[ -n "${RESOLVED_CHECKSUM_URL:-}" ]]; then
            log_info "Downloading checksum ${RESOLVED_CHECKSUM_URL}..."
            download_file "$RESOLVED_CHECKSUM_URL" "$checksum_file"
        fi
    fi

    # Verify checksum if available
    if [[ -n "$checksum_file" && -f "$checksum_file" ]]; then
        verify_checksum "$archive_file" "$checksum_file"
    else
        log_warn "No SHA-256 sidecar found; skipping checksum verification."
    fi

    # Validate archive layout safety
    validate_archive_layout "$archive_file"

    # Extract to staging directory
    local staging_dir="${temp_dir}/staging"
    mkdir -p "$staging_dir"
    log_info "Extracting distribution archive to staging directory..."
    tar -xzf "$archive_file" -C "$staging_dir"

    local bundle_dir="${staging_dir}/Polpo"
    verify_extracted_bundle "$bundle_dir"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "Dry-run mode: verified bundle ${version_id} successfully. Skipping filesystem switch."
        rm -rf "$temp_dir"
        return 0
    fi

    # Atomic installation
    install_bundle "$bundle_dir" "$version_id"

    # Clean up temp directory
    rm -rf "$temp_dir"

    # Run non-destructive dependency check if polpo-deps exists
    if [[ -x "${BIN_DIR}/polpo-deps" ]]; then
        echo ""
        log_info "Checking system GUI dependencies..."
        "${BIN_DIR}/polpo-deps" check || {
            log_warn "Some GUI system libraries may be missing. Run 'polpo-deps install-gui' to install them."
        }
    fi

    return 0
}

main "$@"
