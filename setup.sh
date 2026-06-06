#!/usr/bin/env bash
# Watchdog setup script for macOS
# Usage: bash setup.sh [--with-asr]
#
# --with-asr  Install audio/video transcription support (requires ffmpeg, adds ~2GB)

set -euo pipefail

WATCHDOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_HOME="${HOME}/.watchdog"
COMMANDS_DIR="${HOME}/.claude/commands"
WITH_ASR=false

# Parse flags
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

# ─── 1. Homebrew ──────────────────────────────────────────────────────────────

info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found."
    require_confirm "Install Homebrew?"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Reload PATH for Apple Silicon
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
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
fi
success "Python: $($PYTHON --version)"

# ─── 3. PDF preprocessing tools (qpdf + Ghostscript) ─────────────────────────

info "Installing PDF preprocessing tools..."
for tool in qpdf ghostscript; do
    if ! brew list "$tool" &>/dev/null; then
        brew install "$tool" --quiet
    fi
done
success "qpdf: $(qpdf --version 2>/dev/null | head -1 || echo 'installed')"
success "Ghostscript: $(gs --version 2>/dev/null || echo 'installed')"

# ─── 4. Docling ───────────────────────────────────────────────────────────────

info "Installing Docling..."
if $WITH_ASR; then
    info "  (with ASR/audio support — this may take several minutes)"
    "$PYTHON" -m pip install "docling[asr]" pypdf --quiet
    if ! command -v ffmpeg &>/dev/null; then
        warn "ffmpeg not found — audio/video ingestion requires it."
        require_confirm "Install ffmpeg via Homebrew?"
        brew install ffmpeg
    fi
else
    "$PYTHON" -m pip install docling pypdf --quiet
fi
success "Docling installed"

# On macOS: install ocrmac for Apple Vision OCR (faster than EasyOCR, no extra cost)
if [[ "$(uname)" == "Darwin" ]]; then
    "$PYTHON" -m pip install ocrmac --quiet
    success "ocrmac installed (Apple Vision OCR)"
fi

# ─── 4. Claude Code ───────────────────────────────────────────────────────────

info "Checking Claude Code..."
if ! command -v claude &>/dev/null; then
    warn "Claude Code not found."
    echo ""
    echo "  Install from: https://claude.ai/download"
    echo "  Or via npm:   npm install -g @anthropic-ai/claude-code"
    echo ""
    warn "Claude Code is required. Install it and re-run this script."
    # Don't exit — continue so the rest of the setup is done
fi
if command -v claude &>/dev/null; then
    success "Claude Code: $(claude --version 2>/dev/null | head -1 || echo 'found')"
fi

# ─── 5. Install pipeline scripts ──────────────────────────────────────────────

info "Installing Watchdog pipeline..."
mkdir -p "${WATCHDOG_HOME}/pipeline"
cp "${WATCHDOG_DIR}/pipeline/"*.py "${WATCHDOG_HOME}/pipeline/"
chmod +x "${WATCHDOG_HOME}/pipeline/"*.py
success "Pipeline scripts installed to ${WATCHDOG_HOME}/pipeline/"

# ─── 6. Install skills (Claude Code commands) ─────────────────────────────────

info "Installing Watchdog skills..."
mkdir -p "${COMMANDS_DIR}/records"
cp "${WATCHDOG_DIR}/skills/ingest.md"   "${COMMANDS_DIR}/ingest.md"
cp "${WATCHDOG_DIR}/skills/query.md"    "${COMMANDS_DIR}/query.md"
cp "${WATCHDOG_DIR}/skills/surface.md"  "${COMMANDS_DIR}/surface.md"
cp "${WATCHDOG_DIR}/skills/health.md"   "${COMMANDS_DIR}/health.md"
# Domain knowledge skills
for f in "${WATCHDOG_DIR}/skills/records/"*.md; do
    [[ -f "$f" ]] && cp "$f" "${COMMANDS_DIR}/records/"
done
success "Skills installed to ${COMMANDS_DIR}/"

# ─── 7. Install watchdog CLI ──────────────────────────────────────────────────

info "Installing watchdog CLI..."
mkdir -p "${HOME}/.local/bin"
cp "${WATCHDOG_DIR}/bin/watchdog" "${HOME}/.local/bin/watchdog"
chmod +x "${HOME}/.local/bin/watchdog"
success "CLI installed to ${HOME}/.local/bin/watchdog"

# ─── 8. Shell profile setup ───────────────────────────────────────────────────

# Detect shell profile file
detect_profile() {
    if [[ -n "${BASH_VERSION:-}" ]]; then
        [[ -f "${HOME}/.bash_profile" ]] && echo "${HOME}/.bash_profile" || echo "${HOME}/.bashrc"
    elif [[ -n "${ZSH_VERSION:-}" ]]; then
        echo "${HOME}/.zshrc"
    elif [[ -f "${HOME}/.zshrc" ]]; then
        echo "${HOME}/.zshrc"
    elif [[ -f "${HOME}/.bash_profile" ]]; then
        echo "${HOME}/.bash_profile"
    else
        echo "${HOME}/.profile"
    fi
}

PROFILE="$(detect_profile)"
info "Configuring shell profile: ${PROFILE}"

# PATH addition + WATCHDOG_PIPELINE env var
PATH_LINE='export PATH="${HOME}/.local/bin:${PATH}"'
if ! grep -qF '.local/bin' "${PROFILE}" 2>/dev/null; then
    printf '\n# Watchdog\n%s\nexport WATCHDOG_PIPELINE="${HOME}/.watchdog/pipeline"\n' \
        "${PATH_LINE}" >> "${PROFILE}"
    info "  Added ~/.local/bin to PATH and WATCHDOG_PIPELINE in ${PROFILE}"
elif ! grep -q 'WATCHDOG_PIPELINE' "${PROFILE}" 2>/dev/null; then
    echo 'export WATCHDOG_PIPELINE="${HOME}/.watchdog/pipeline"' >> "${PROFILE}"
    info "  Added WATCHDOG_PIPELINE to ${PROFILE}"
fi

# Tab completion for zsh
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

# Tab completion for bash
if [[ "${PROFILE}" == *".bashrc" || "${PROFILE}" == *".bash_profile" ]]; then
    BASH_COMPLETION='
# Watchdog tab completion
_watchdog_bash_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=($(compgen -W "new open list" -- "$cur"))
    elif [[ "$prev" == "open" ]]; then
        local projects
        projects=$(python3 -c "
import json, os
p = os.path.expanduser(\"~/.watchdog/projects.json\")
if os.path.exists(p):
    data = json.load(open(p))
    print(\" \".join(data.keys()))
" 2>/dev/null)
        COMPREPLY=($(compgen -W "$projects" -- "$cur"))
    fi
}
complete -F _watchdog_bash_complete watchdog'

    if ! grep -q '_watchdog_bash_complete' "${PROFILE}" 2>/dev/null; then
        echo "${BASH_COMPLETION}" >> "${PROFILE}"
        info "  Added bash tab completion"
    fi
fi

success "Shell profile configured"

# ─── 9. Create projects directory ─────────────────────────────────────────────

if [[ ! -d "${HOME}/Investigations" ]]; then
    info "Creating ~/Investigations/ ..."
    mkdir -p "${HOME}/Investigations"
    success "~/Investigations/ created — your vaults will go here by default"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Watchdog is installed."
echo ""
echo "  Reload your shell or run:"
echo "    source ${PROFILE}"
echo ""
echo "  Then create your first investigation:"
echo "    watchdog new \"My Investigation\""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
