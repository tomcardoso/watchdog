#!/usr/bin/env bash
# Watchdog setup script for macOS
# Usage: bash setup.sh [--with-asr]
#
# --with-asr  Install audio/video transcription support (requires ffmpeg, adds ~2GB)

set -euo pipefail

WATCHDOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMANDS_DIR="${HOME}/.claude/commands"
WITH_ASR=false

for arg in "$@"; do
    [[ "$arg" == "--with-asr" ]] && WITH_ASR=true
done

# ─── Helpers ──────────────────────────────────────────────────────────────────

info()    { printf '\033[0;34m→\033[0m  %s\n' "$*"; }
success() { printf '\033[0;32m✓\033[0m  %s\n' "$*"; }
warn()    { printf '\033[0;33m!\033[0m  %s\n' "$*"; }
die()     { printf '\033[0;31m✗\033[0m  %s\n' "$*" >&2; exit 1; }

require_confirm() {
    local prompt="$1"
    read -rp "$prompt [y/N] " answer
    [[ "${answer,,}" == "y" ]] || die "Aborted."
}

# ─── Banner ───────────────────────────────────────────────────────────────────

printf '\n'
printf '      / \__\n'
printf '     (    @\___\n'
printf '     /         O\n'
printf '    /   (_____/\n'
printf '   /_____/   U\n'
printf '\n'
printf '  \033[1mWatchdog\033[0m — investigative journalism document intelligence\n'
printf '  \033[0;34mhttps://github.com/tomcardoso/watchdog\033[0m\n'
printf '\n'
printf '  Drop public records into a folder. Watchdog extracts every person,\n'
printf '  company, address, and relationship it finds, stores them as structured\n'
printf '  notes in an Obsidian vault, and surfaces connections you might have missed.\n'
printf '\n'
printf '  This script will install:\n'
printf '    • Homebrew packages: Python 3, qpdf, ghostscript, pipx\n'
printf '    • Python packages:   Docling, pypdf (+ ocrmac on macOS for faster OCR)\n'
printf '    • Claude Code skills: /ingest, /query, /surface, /health\n'
printf '    • The \033[1mwatchdog\033[0m CLI command\n'
if $WITH_ASR; then
printf '    • Audio/video transcription support (--with-asr)\n'
fi
printf '\n'
printf '  Nothing will be installed without your confirmation.\n'
printf '  Press Ctrl-C at any time to abort.\n'
printf '\n'
read -rp "  Ready to install? [y/N] " answer
[[ "${answer,,}" == "y" ]] || { echo "Aborted."; exit 0; }
printf '\n'

# ─── 1. Homebrew ──────────────────────────────────────────────────────────────

info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found."
    require_confirm "Install Homebrew?"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
fi
success "Homebrew: $(brew --version | head -1)"

# ─── 2. Python ────────────────────────────────────────────────────────────────

info "Checking Python 3.10+..."
PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=${ver%%.*}; minor=${ver##*.}
        if (( major >= 3 && minor >= 10 )); then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.10+ not found."
    require_confirm "Install Python 3.12 via Homebrew?"
    brew install python@3.12 --quiet
    PYTHON="$(brew --prefix)/bin/python3.12"
fi
success "Python: $($PYTHON --version)"

# ─── 3. PDF preprocessing tools ───────────────────────────────────────────────

info "Installing PDF preprocessing tools (qpdf, ghostscript)..."
for tool in qpdf ghostscript; do
    if ! brew list "$tool" &>/dev/null; then
        brew install "$tool" --quiet
    fi
done
success "qpdf: $(qpdf --version 2>/dev/null | head -1 || echo 'installed')"
success "Ghostscript: $(gs --version 2>/dev/null || echo 'installed')"

# ─── 4. pipx ──────────────────────────────────────────────────────────────────

info "Checking pipx..."
if ! command -v pipx &>/dev/null; then
    brew install pipx --quiet
    pipx ensurepath
fi
success "pipx: $(pipx --version)"

# ─── 5. Watchdog Python package ───────────────────────────────────────────────

info "Installing Watchdog..."
if $WITH_ASR; then
    info "  (with ASR/audio support — this may take several minutes)"
    PIPX_EXTRA="[asr]"
else
    PIPX_EXTRA=""
fi

pipx install "${WATCHDOG_DIR}${PIPX_EXTRA}" --force --quiet
success "Watchdog installed"

# On macOS: inject ocrmac for Apple Vision OCR (faster than EasyOCR on Apple Silicon)
if [[ "$(uname)" == "Darwin" ]]; then
    pipx inject watchdog-intel ocrmac --quiet 2>/dev/null && \
        success "ocrmac injected (Apple Vision OCR)" || \
        warn "ocrmac injection skipped (non-critical)"
fi

# If --with-asr, check for ffmpeg
if $WITH_ASR && ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not found — audio/video ingestion requires it."
    require_confirm "Install ffmpeg via Homebrew?"
    brew install ffmpeg
fi

# ─── 6. Claude Code ───────────────────────────────────────────────────────────

info "Checking Claude Code..."
if ! command -v claude &>/dev/null; then
    warn "Claude Code not found."
    printf '\n'
    printf '  Install from: https://claude.ai/download\n'
    printf '  Or via npm:   npm install -g @anthropic-ai/claude-code\n\n'
    warn "Install Claude Code then re-run this script to complete setup."
fi
if command -v claude &>/dev/null; then
    success "Claude Code: $(claude --version 2>/dev/null | head -1 || echo 'found')"
fi

# ─── 7. Claude Code skills ────────────────────────────────────────────────────

info "Installing Watchdog skills..."
mkdir -p "${COMMANDS_DIR}/records"
cp "${WATCHDOG_DIR}/skills/ingest.md"   "${COMMANDS_DIR}/ingest.md"
cp "${WATCHDOG_DIR}/skills/query.md"    "${COMMANDS_DIR}/query.md"
cp "${WATCHDOG_DIR}/skills/surface.md"  "${COMMANDS_DIR}/surface.md"
cp "${WATCHDOG_DIR}/skills/health.md"   "${COMMANDS_DIR}/health.md"
for f in "${WATCHDOG_DIR}/skills/records/"*.md; do
    [[ -f "$f" ]] && cp "$f" "${COMMANDS_DIR}/records/"
done
success "Skills installed to ${COMMANDS_DIR}/"

# ─── 8. Shell profile ─────────────────────────────────────────────────────────

detect_profile() {
    if [[ -n "${ZSH_VERSION:-}" ]] || [[ -f "${HOME}/.zshrc" ]]; then
        echo "${HOME}/.zshrc"
    elif [[ -f "${HOME}/.bash_profile" ]]; then
        echo "${HOME}/.bash_profile"
    else
        echo "${HOME}/.profile"
    fi
}

PROFILE="$(detect_profile)"
info "Configuring shell profile: ${PROFILE}"

# pipx ensurepath handles PATH; add tab completion
if [[ "${PROFILE}" == *".zshrc" ]]; then
    COMPLETION_SNIPPET='
# Watchdog tab completion
_watchdog_complete() {
    local -a subcmds
    subcmds=(new open list)
    if (( CURRENT == 2 )); then
        compadd -a subcmds
    elif (( CURRENT == 3 )) && [[ "${words[2]}" == "open" ]]; then
        local projects
        projects=$(python3 -c "
import json, os
p = os.path.expanduser(\"~/.watchdog/projects.json\")
if os.path.exists(p):
    data = json.load(open(p))
    print(\" \".join(data.keys()))
" 2>/dev/null)
        compadd ${=projects}
    fi
}
compdef _watchdog_complete watchdog'

    if ! grep -q '_watchdog_complete' "${PROFILE}" 2>/dev/null; then
        echo "${COMPLETION_SNIPPET}" >> "${PROFILE}"
        info "  Added zsh tab completion"
    fi
fi

success "Shell profile configured"

# ─── 9. Projects directory ────────────────────────────────────────────────────

if [[ ! -d "${HOME}/Investigations" ]]; then
    mkdir -p "${HOME}/Investigations"
    success "~/Investigations/ created — your vaults will live here by default"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

printf '\n'
printf '  \033[0;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n'
printf '  \033[1m  Watchdog is installed.\033[0m\n'
printf '\n'
printf '  Reload your shell:\n'
printf '    \033[1msource %s\033[0m\n' "${PROFILE}"
printf '\n'
printf '  Then create your first investigation:\n'
printf '    \033[1mwatchdog new "My Investigation"\033[0m\n'
printf '  \033[0;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n'
printf '\n'
