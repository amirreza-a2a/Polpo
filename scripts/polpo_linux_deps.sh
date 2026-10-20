#!/usr/bin/env bash
# ==============================================================================
# Polpo Linux System Dependency Helper
#
# Inspects, validates, and helps install system-level GUI libraries (PySide6/Qt)
# and optional PDF export prerequisites (Quarto, XeLaTeX) across supported
# Linux distributions: Debian/Ubuntu, Fedora, Arch Linux, and openSUSE.
#
# Safety Guarantees:
# - Default 'check', 'gui', and 'pdf' modes are strictly read-only and non-destructive.
# - Never invokes sudo or package managers without explicit user confirmation or -y.
# - Does not install development toolchains, Node.js, Pandoc, or Python.
# - Quoted arguments prevent shell injection.
# ==============================================================================

set -euo pipefail

# Default configuration
OS_RELEASE_PATH="${POLPO_TEST_OS_RELEASE:-/etc/os-release}"
ASSUME_YES=false
DRY_RUN=false
COMMAND=""

print_usage() {
    cat << 'EOF'
Usage: polpo-deps [COMMAND] [OPTIONS]

Commands:
  check        Check system GUI library dependencies (read-only, non-destructive)
  gui          Alias for 'check'
  pdf          Check optional PDF export prerequisites (Quarto, XeLaTeX)
  install-gui  Install missing system GUI libraries (prompts for consent)
  install-pdf  Install optional PDF export TeX packages (prompts for consent)
  --help, -h   Show this help message

Options:
  -y, --yes          Assume yes to prompts; non-interactive installation
  --dry-run          Print package-manager command without executing it
  --os-release PATH  Specify custom os-release path (for testing)

Exit Codes:
  0  All checked dependencies are satisfied, or command succeeded
  1  One or more dependencies are missing, or user declined installation
  2  Unsupported architecture or libc
EOF
}

# ------------------------------------------------------------------------------
# Preflight checks: Architecture and Libc
# ------------------------------------------------------------------------------

check_arch() {
    local arch
    arch="$(uname -m)"
    if [[ "$arch" != "x86_64" && "$arch" != "amd64" ]]; then
        echo "Error: Polpo Linux distributions only support x86_64 architecture." >&2
        echo "Detected architecture: ${arch}" >&2
        return 2
    fi
}

check_libc() {
    # Check for Alpine / musl
    if [[ -f /etc/alpine-release ]] || (command -v ldd >/dev/null 2>&1 && ldd /bin/ls 2>&1 | grep -iq "musl"); then
        echo "Error: Polpo requires glibc and is not compatible with musl-based distributions (e.g., Alpine Linux)." >&2
        return 2
    fi
}

# ------------------------------------------------------------------------------
# Distribution Detection
# ------------------------------------------------------------------------------

detect_distro() {
    local os_rel="$1"
    DISTRO_ID="unknown"
    DISTRO_LIKE=""
    DISTRO_NAME="Unknown Linux"
    DISTRO_FAMILY="unknown"

    if [[ -f "$os_rel" ]]; then
        # Parse os-release safely without full evaluation
        while IFS='=' read -r key value || [[ -n "$key" ]]; do
            # Remove leading/trailing quotes and whitespace
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            case "$key" in
                ID) DISTRO_ID="$(echo "$value" | tr '[:upper:]' '[:lower:]')" ;;
                ID_LIKE) DISTRO_LIKE="$(echo "$value" | tr '[:upper:]' '[:lower:]')" ;;
                PRETTY_NAME) DISTRO_NAME="$value" ;;
                NAME) [[ "$DISTRO_NAME" == "Unknown Linux" ]] && DISTRO_NAME="$value" ;;
            esac
        done < "$os_rel"
    fi

    # Classify distro family
    local combined="${DISTRO_ID} ${DISTRO_LIKE}"
    if [[ "$combined" =~ (ubuntu|debian|linuxmint|pop|elementary|kali|zorin|raspbian) ]]; then
        DISTRO_FAMILY="debian"
    elif [[ "$combined" =~ (fedora|rhel|centos|rocky|alma) ]]; then
        DISTRO_FAMILY="fedora"
    elif [[ "$combined" =~ (arch|manjaro|endeavouros|garuda|artix) ]]; then
        DISTRO_FAMILY="arch"
    elif [[ "$combined" =~ (suse|opensuse|opensuse-tumbleweed|opensuse-leap) ]]; then
        DISTRO_FAMILY="suse"
    else
        DISTRO_FAMILY="unknown"
    fi
}

# ------------------------------------------------------------------------------
# Package and Library Definitions per Distro Family
# ------------------------------------------------------------------------------

get_gui_packages() {
    local family="$1"
    case "$family" in
        debian)
            echo "libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3"
            ;;
        fedora)
            echo "mesa-libGL mesa-libEGL libxkbcommon-x11 dbus-libs"
            ;;
        arch)
            echo "libglvnd libxkbcommon-x11 dbus"
            ;;
        suse)
            echo "libGL1 libEGL1 libxkbcommon-x11-0 libdbus-1-3"
            ;;
        *)
            echo ""
            ;;
    esac
}

get_pdf_packages() {
    local family="$1"
    case "$family" in
        debian)
            echo "texlive-xetex texlive-latex-extra"
            ;;
        fedora)
            echo "texlive-xetex texlive-collection-latexextra"
            ;;
        arch)
            echo "texlive-xetex"
            ;;
        suse)
            echo "texlive-xetex"
            ;;
        *)
            echo ""
            ;;
    esac
}

# ------------------------------------------------------------------------------
# Package Status Inspection
# ------------------------------------------------------------------------------

is_pkg_installed() {
    local family="$1"
    local pkg="$2"

    case "$family" in
        debian)
            if command -v dpkg-query >/dev/null 2>&1; then
                local status
                status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)"
                [[ "$status" =~ "install ok installed" ]] && return 0
            fi
            return 1
            ;;
        fedora)
            if command -v rpm >/dev/null 2>&1; then
                rpm -q "$pkg" >/dev/null 2>&1 && return 0
            fi
            return 1
            ;;
        arch)
            if command -v pacman >/dev/null 2>&1; then
                pacman -Q "$pkg" >/dev/null 2>&1 && return 0
            fi
            return 1
            ;;
        suse)
            if command -v rpm >/dev/null 2>&1; then
                rpm -q "$pkg" >/dev/null 2>&1 && return 0
            fi
            return 1
            ;;
        *)
            return 1
            ;;
    esac
}

# Fallback: check shared libraries directly via ldconfig or filesystem
has_shared_library() {
    local lib_name="$1"
    if command -v ldconfig >/dev/null 2>&1; then
        if ldconfig -p 2>/dev/null | grep -F -q "$lib_name"; then
            return 0
        fi
    fi
    for search_dir in /usr/lib /usr/lib64 /lib /lib64 /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu; do
        if [[ -e "${search_dir}/${lib_name}" ]]; then
            return 0
        fi
    done
    return 1
}

# ------------------------------------------------------------------------------
# GUI Check & Install Logic
# ------------------------------------------------------------------------------

run_gui_check() {
    echo "=================================================================="
    echo "Polpo GUI System Dependencies Check"
    echo "=================================================================="
    echo "Distribution : ${DISTRO_NAME}"
    echo "Distro family: ${DISTRO_FAMILY} (ID: ${DISTRO_ID})"
    echo ""

    local pkgs
    pkgs="$(get_gui_packages "$DISTRO_FAMILY")"
    local missing_pkgs=()

    if [[ -z "$pkgs" ]]; then
        echo "Note: Unrecognized distribution family. Checking required shared libraries:"
        local required_libs=("libGL.so.1" "libEGL.so.1" "libxkbcommon-x11.so.0" "libdbus-1.so.3")
        local missing_libs=()
        for lib in "${required_libs[@]}"; do
            if has_shared_library "$lib"; then
                echo "  [OK]      ${lib} (found in system library cache)"
            else
                echo "  [MISSING] ${lib}"
                missing_libs+=("$lib")
            fi
        done
        echo ""
        if [[ ${#missing_libs[@]} -eq 0 ]]; then
            echo "Status: All required GUI shared libraries are present."
            return 0
        else
            echo "Status: Missing ${#missing_libs[@]} required shared libraries: ${missing_libs[*]}"
            echo "Please use your distribution's package manager to install packages providing these libraries."
            return 1
        fi
    fi

    # Known family: verify each package
    for pkg in $pkgs; do
        if is_pkg_installed "$DISTRO_FAMILY" "$pkg"; then
            echo "  [OK]      ${pkg} (installed)"
        else
            echo "  [MISSING] ${pkg}"
            missing_pkgs+=("$pkg")
        fi
    done

    echo ""
    if [[ ${#missing_pkgs[@]} -eq 0 ]]; then
        echo "Status: All required GUI dependencies are installed."
        return 0
    else
        echo "Status: ${#missing_pkgs[@]} required GUI package(s) missing: ${missing_pkgs[*]}"
        echo ""
        echo "To install missing dependencies automatically, run:"
        echo "  polpo-deps install-gui"
        echo ""
        echo "Or run the command manually:"
        print_gui_install_cmd "${missing_pkgs[@]}"
        return 1
    fi
}

print_gui_install_cmd() {
    local missing=("$@")
    case "$DISTRO_FAMILY" in
        debian)
            echo "  sudo apt-get update && sudo apt-get install -y ${missing[*]}"
            ;;
        fedora)
            echo "  sudo dnf install -y ${missing[*]}"
            ;;
        arch)
            echo "  sudo pacman -S --needed ${missing[*]}"
            ;;
        suse)
            echo "  sudo zypper install -y ${missing[*]}"
            ;;
        *)
            echo "  (Use your package manager to install: ${missing[*]})"
            ;;
    esac
}

run_gui_install() {
    local pkgs
    pkgs="$(get_gui_packages "$DISTRO_FAMILY")"
    if [[ -z "$pkgs" ]]; then
        echo "Error: Cannot automatically install packages for unknown distribution family: ${DISTRO_ID}." >&2
        echo "Please install OpenGL, EGL, xkbcommon-x11, and dbus using your distribution package manager." >&2
        return 1
    fi

    local missing_pkgs=()
    for pkg in $pkgs; do
        if ! is_pkg_installed "$DISTRO_FAMILY" "$pkg"; then
            missing_pkgs+=("$pkg")
        fi
    done

    if [[ ${#missing_pkgs[@]} -eq 0 ]]; then
        echo "All required GUI dependencies are already satisfied. No packages to install."
        return 0
    fi

    echo "Polpo GUI Dependency Installation"
    echo "---------------------------------"
    echo "The following missing system packages will be installed:"
    for p in "${missing_pkgs[@]}"; do
        echo "  - $p"
    done
    echo ""

    local cmd_str=""
    case "$DISTRO_FAMILY" in
        debian)
            cmd_str="sudo apt-get update && sudo apt-get install -y ${missing_pkgs[*]}"
            ;;
        fedora)
            cmd_str="sudo dnf install -y ${missing_pkgs[*]}"
            ;;
        arch)
            cmd_str="sudo pacman -S --needed ${missing_pkgs[*]}"
            ;;
        suse)
            cmd_str="sudo zypper install -y ${missing_pkgs[*]}"
            ;;
    esac

    echo "Command to be executed:"
    echo "  ${cmd_str}"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "Dry-run mode: skipping execution."
        return 0
    fi

    if [[ "$ASSUME_YES" != "true" ]]; then
        local reply=""
        echo -n "Do you want to proceed with installing these packages? [y/N] "
        if read -r reply; then
            :
        elif [[ -t 1 && -r /dev/tty && -c /dev/tty ]]; then
            read -r reply </dev/tty || reply="n"
        else
            echo ""
            echo "Error: Standard input is not a terminal and no input was provided. Pass --yes (-y) to confirm installation." >&2
            return 1
        fi
        if [[ ! "$reply" =~ ^[Yy]$ ]]; then
            echo "Installation cancelled by user."
            return 1
        fi
    fi

    echo "Executing package manager command with elevated privileges..."
    case "$DISTRO_FAMILY" in
        debian)
            sudo apt-get update && sudo apt-get install -y "${missing_pkgs[@]}"
            ;;
        fedora)
            sudo dnf install -y "${missing_pkgs[@]}"
            ;;
        arch)
            sudo pacman -S --needed --noconfirm "${missing_pkgs[@]}"
            ;;
        suse)
            sudo zypper install -y "${missing_pkgs[@]}"
            ;;
    esac
}

# ------------------------------------------------------------------------------
# PDF Prerequisites Check & Install Logic
# ------------------------------------------------------------------------------

run_pdf_check() {
    echo "=================================================================="
    echo "Polpo PDF Export Prerequisites Check (Optional Feature)"
    echo "=================================================================="
    echo "Note: PDF export is an optional feature. Core transcription, OCR,"
    echo "Markdown editing, and MathJax preview do NOT require Quarto or XeLaTeX."
    echo ""

    local quarto_ok=false
    local xelatex_ok=false
    local quarto_ver=""

    # 1. Check Quarto
    if command -v quarto >/dev/null 2>&1; then
        quarto_ver="$(quarto --version 2>/dev/null || true)"
        if [[ -n "$quarto_ver" ]]; then
            # Parse version components (semver)
            local v_major v_minor
            v_major=$(echo "$quarto_ver" | cut -d. -f1 | grep -o '[0-9]*' | head -n1 || echo 0)
            v_minor=$(echo "$quarto_ver" | cut -d. -f2 | grep -o '[0-9]*' | head -n1 || echo 0)
            if (( v_major > 1 || (v_major == 1 && v_minor >= 4) )); then
                echo "  [OK]      Quarto CLI: version ${quarto_ver} (meets requirement >= 1.4.0)"
                quarto_ok=true
            else
                echo "  [WARN]    Quarto CLI: version ${quarto_ver} installed, but Polpo requires >= 1.4.0"
            fi
        else
            echo "  [WARN]    Quarto CLI found, but failed to probe version"
        fi
    else
        echo "  [MISSING] Quarto CLI (>= 1.4.0 required for PDF rendering)"
    fi

    # 2. Check XeLaTeX
    local xelatex_path=""
    if command -v xelatex >/dev/null 2>&1; then
        xelatex_path="$(command -v xelatex)"
    elif [[ -x "$HOME/.TinyTeX/bin/x86_64-linux/xelatex" ]]; then
        xelatex_path="$HOME/.TinyTeX/bin/x86_64-linux/xelatex"
    fi

    if [[ -n "$xelatex_path" ]]; then
        local dist_type="TeX Live"
        if [[ "$xelatex_path" =~ [Tt]iny[Tt]e[Xx] ]]; then
            dist_type="TinyTeX"
        fi
        echo "  [OK]      XeLaTeX compiler: ${xelatex_path} (${dist_type})"
        xelatex_ok=true
    else
        echo "  [MISSING] XeLaTeX compiler (required for PDF typesetting)"
    fi

    echo ""

    if [[ "$quarto_ok" == "true" && "$xelatex_ok" == "true" ]]; then
        echo "Status: All PDF export prerequisites are satisfied."
        return 0
    fi

    echo "Installation Guidance for Missing PDF Prerequisites:"
    echo "----------------------------------------------------"

    if [[ "$quarto_ok" != "true" ]]; then
        echo "* Quarto CLI:"
        case "$DISTRO_FAMILY" in
            debian)
                echo "  Download and install the official Quarto .deb package from:"
                echo "    https://quarto.org/docs/get-started/"
                echo "  Example:"
                echo "    sudo dpkg -i quarto-<version>-linux-amd64.deb"
                ;;
            arch)
                echo "  Install from AUR or official tarball:"
                echo "    yay -S quarto-cli-bin"
                echo "  Or see: https://quarto.org/docs/get-started/"
                ;;
            *)
                echo "  Download and extract the official Quarto Linux tarball from:"
                echo "    https://quarto.org/docs/get-started/"
                ;;
        esac
        echo ""
    fi

    if [[ "$xelatex_ok" != "true" ]]; then
        echo "* XeLaTeX / TeX Distribution (Storage Options):"
        echo "  Option A (Recommended, ~200MB):"
        echo "    Use Polpo Desktop's built-in PDF Setup Assistant, or run:"
        echo "      quarto install tinytex"
        echo "  Option B (System TeX packages, ~500MB - 1GB):"
        case "$DISTRO_FAMILY" in
            debian)
                echo "    sudo apt-get install -y texlive-xetex texlive-latex-extra"
                ;;
            fedora)
                echo "    sudo dnf install -y texlive-xetex texlive-collection-latexextra"
                ;;
            arch)
                echo "    sudo pacman -S texlive-xetex"
                ;;
            suse)
                echo "    sudo zypper install -y texlive-xetex"
                ;;
            *)
                echo "    Use your distribution package manager to install texlive-xetex"
                ;;
        esac
        echo "  Note: Do NOT install 'texlive-full' unless you explicitly want >4GB of fonts and packages."
        echo ""
    fi

    return 1
}

run_pdf_install() {
    local pdf_pkgs
    pdf_pkgs="$(get_pdf_packages "$DISTRO_FAMILY")"
    if [[ -z "$pdf_pkgs" ]]; then
        echo "Error: Cannot automatically install TeX packages for unknown distribution: ${DISTRO_ID}." >&2
        return 1
    fi

    echo "Polpo Optional PDF Export Dependency Installation"
    echo "================================================="
    echo "Notice on Storage Requirements:"
    echo "  - Distro TeX packages require approx. 500MB to 1GB of disk space."
    echo "  - Alternatively, TinyTeX (~200MB) can be installed via 'quarto install tinytex'"
    echo "    or the in-app PDF Setup Assistant inside Polpo Desktop."
    echo ""
    echo "The following system packages would be installed:"
    echo "  ${pdf_pkgs}"
    echo ""

    local cmd_str=""
    case "$DISTRO_FAMILY" in
        debian)
            cmd_str="sudo apt-get update && sudo apt-get install -y ${pdf_pkgs}"
            ;;
        fedora)
            cmd_str="sudo dnf install -y ${pdf_pkgs}"
            ;;
        arch)
            cmd_str="sudo pacman -S --needed ${pdf_pkgs}"
            ;;
        suse)
            cmd_str="sudo zypper install -y ${pdf_pkgs}"
            ;;
    esac

    echo "Command to be executed:"
    echo "  ${cmd_str}"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "Dry-run mode: skipping execution."
        return 0
    fi

    if [[ "$ASSUME_YES" != "true" ]]; then
        local reply=""
        echo -n "Do you want to proceed with installing system TeX packages? [y/N] "
        if read -r reply; then
            :
        elif [[ -t 1 && -r /dev/tty && -c /dev/tty ]]; then
            read -r reply </dev/tty || reply="n"
        else
            echo ""
            echo "Error: Standard input is not a terminal and no input was provided. Pass --yes (-y) to confirm installation." >&2
            return 1
        fi
        if [[ ! "$reply" =~ ^[Yy]$ ]]; then
            echo "Installation cancelled by user."
            return 1
        fi
    fi

    local -a pkgs_array=()
    read -ra pkgs_array <<< "$pdf_pkgs"

    echo "Executing package manager command with elevated privileges..."
    case "$DISTRO_FAMILY" in
        debian)
            sudo apt-get update && sudo apt-get install -y "${pkgs_array[@]}"
            ;;
        fedora)
            sudo dnf install -y "${pkgs_array[@]}"
            ;;
        arch)
            sudo pacman -S --needed --noconfirm "${pkgs_array[@]}"
            ;;
        suse)
            sudo zypper install -y "${pkgs_array[@]}"
            ;;
    esac
}

# ------------------------------------------------------------------------------
# Argument Parsing & Dispatch
# ------------------------------------------------------------------------------

main() {
    check_arch
    check_libc

    while [[ $# -gt 0 ]]; do
        case "$1" in
            check|gui|pdf|install-gui|install-pdf)
                if [[ -n "$COMMAND" ]]; then
                    echo "Error: Conflicting commands: '$COMMAND' and '$1'" >&2
                    print_usage
                    return 1
                fi
                COMMAND="$1"
                shift
                ;;
            -y|--yes)
                ASSUME_YES=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --os-release)
                if [[ $# -lt 2 ]]; then
                    echo "Error: --os-release requires a path argument" >&2
                    return 1
                fi
                OS_RELEASE_PATH="$2"
                shift 2
                ;;
            -h|--help)
                print_usage
                return 0
                ;;
            *)
                echo "Error: Unknown argument '$1'" >&2
                print_usage
                return 1
                ;;
        esac
    done

    # Default command is 'check'
    if [[ -z "$COMMAND" ]]; then
        COMMAND="check"
    fi

    detect_distro "$OS_RELEASE_PATH"

    case "$COMMAND" in
        check|gui)
            run_gui_check
            ;;
        install-gui)
            run_gui_install
            ;;
        pdf)
            run_pdf_check
            ;;
        install-pdf)
            run_pdf_install
            ;;
        *)
            echo "Error: Unrecognized command '$COMMAND'" >&2
            print_usage
            return 1
            ;;
    esac
}

main "$@"
