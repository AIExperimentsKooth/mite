#!/usr/bin/env bash
# ============================================================================
# Mite Update Script
# ============================================================================
# Backs up userdata (~/.mite/), fetches the latest code from GitHub, restores
# userdata, and re-runs setup.
#
# Preserved userdata (full ~/.mite/ directory):
#   config.json, AGENT.md, conversations/, queue.json, schedule.json
#
# Usage:
#   bash update.sh                  # Interactive
#   bash update.sh --yes            # Non-interactive (auto-confirm)
#   bash update.sh --dry-run        # Show what would happen without doing it
#   bash update.sh --branch dev     # Update from the dev branch instead of main
#   bash update.sh --install-dir /path/to/mite  # Specify install directory
# ============================================================================
set -euo pipefail

# --- Config ----------------------------------------------------------------
REPO_URL="https://github.com/AIExperimentsKooth/mite.git"
BRANCH="main"

# --- Helpers ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%s)
BACKUP_DIR="/tmp/mite-backup-${TIMESTAMP}"
DRY_RUN=false
AUTO_CONFIRM=false
INSTALL_DIR=""

# Cleanup trap: restore backup on unexpected failure
_cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ] && [ -d "$BACKUP_DIR" ] && [ -z "$DRY_RUN" ]; then
        echo
        err "Update failed (exit $exit_code). Restoring backup..."
        if [ -d "$HOME/.mite" ]; then
            rm -rf "$HOME/.mite"
        fi
        cp -r "$BACKUP_DIR" "$HOME/.mite"
        ok "~/.mite/ restored from backup"
    fi
}
trap _cleanup EXIT

# Color helpers
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}\u2139${NC} $1"; }
ok()    { echo -e "${GREEN}\u2713${NC} $1"; }
warn()  { echo -e "${YELLOW}\u26a0${NC} $1"; }
err()   { echo -e "${RED}\u2717${NC} $1"; }
header(){ echo; echo -e "${BOLD}$1${NC}"; echo "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"; }

# --- Argument parsing ------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)        AUTO_CONFIRM=true; shift ;;
        --dry-run)       DRY_RUN=true; shift ;;
        --branch)        BRANCH="$2"; shift 2 ;;
        --branch=*)      BRANCH="${1#*=}"; shift ;;
        --install-dir)   INSTALL_DIR="$2"; shift 2 ;;
        --install-dir=*) INSTALL_DIR="${1#*=}"; shift ;;
        --help|-h)       sed -n '/^# Usage:/,/^# ====/p' "$0" | sed 's/^# //'; exit 0 ;;
        *)               err "Unknown option: $1"; exit 1 ;;
    esac
done

# Determine install directory
if [ -z "$INSTALL_DIR" ]; then
    # Default: the directory containing this script
    INSTALL_DIR="$SCRIPT_DIR"
fi

# Resolve to absolute path
INSTALL_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd || echo "$INSTALL_DIR")"

# Make sure update.sh is inside the install dir (or it's a fresh install)
FRESH_INSTALL=false
if [ ! -f "$INSTALL_DIR/update.sh" ]; then
    FRESH_INSTALL=true
fi

header "Mite Update Script"
echo "  Repo:       $REPO_URL"
echo "  Branch:     $BRANCH"
echo "  Install:    $INSTALL_DIR"
echo "  Userdata:   $HOME/.mite/ (config, AGENT.md, conversations, queue, schedule)"

if $DRY_RUN; then
    echo
    info "${BOLD}[DRY RUN]${NC} No changes will be made."
fi

# --- Step 1: Backup userdata ------------------------------------------------
header "Step 1: Backup userdata (~/.mite/)"

if [ -d "$HOME/.mite" ]; then
    if $DRY_RUN; then
        info "Would backup ~/.mite/ \u2192 $BACKUP_DIR"
    else
        info "Backing up ~/.mite/..."
        cp -r "$HOME/.mite" "$BACKUP_DIR"
        ok "Backed up to $BACKUP_DIR"
    fi
else
    info "No ~/.mite/ found \u2014 nothing to back up."
fi

# --- Step 2: Confirm --------------------------------------------------------
header "Step 2: Confirm update"

if ! $AUTO_CONFIRM; then
    if $DRY_RUN; then
        echo "  (dry run \u2014 skipping confirmation)"
    else
        if $FRESH_INSTALL; then
            echo "  This will install Mite to: ${INSTALL_DIR}"
        else
            echo "  This will update Mite at: ${INSTALL_DIR}"
        fi
        echo "  Existing ~/.mite/ userdata will be preserved."
        read -r -p "  Continue? [Y/n]: " REPLY
        if [[ "$REPLY" =~ ^[Nn] ]]; then
            warn "Update cancelled."
            exit 0
        fi
    fi
fi

# --- Step 3: Fetch latest code ---------------------------------------------
header "Step 3: Fetch latest code"

fetch_via_git() {
    local dir="$1"
    cd "$dir"

    # Check if there's a git remote
    if git remote -v 2>/dev/null | grep -q .; then
        info "Using existing git remote..."
        if $DRY_RUN; then
            info "Would run: git fetch --all && git reset --hard origin/$BRANCH"
        else
            git fetch --all --quiet 2>&1 || true
            git reset --hard "origin/$BRANCH" --quiet
            ok "Updated to latest commit via git pull"
        fi
        return 0
    fi
    return 1
}

fetch_via_clone() {
    local target="$1"
    local tmp_dir="/tmp/mite-fresh-${TIMESTAMP}"
    local clone_url="$REPO_URL"

    if $DRY_RUN; then
        info "Would clone $REPO_URL \u2192 $tmp_dir"
        info "Would copy $tmp_dir/ \u2192 $target/"
        return
    fi

    # Try to find GitHub credentials for private repo access
    local gh_token=""

    # Strategy 1: gh CLI (GitHub official CLI)
    if command -v gh &>/dev/null; then
        if gh auth status &>/dev/null; then
            info "Using gh CLI authentication..."
            clone_url="https://$(gh auth token)@github.com/${REPO_URL#https://github.com/}"
        fi
    fi

    # Strategy 2: GITHUB_TOKEN or GH_TOKEN env var
    if [ -z "$gh_token" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
        gh_token="$GITHUB_TOKEN"
        info "Using GITHUB_TOKEN for authentication..."
        clone_url="https://oauth2:${gh_token}@github.com/${REPO_URL#https://github.com/}"
    elif [ -z "$gh_token" ] && [ -n "${GH_TOKEN:-}" ]; then
        gh_token="$GH_TOKEN"
        info "Using GH_TOKEN for authentication..."
        clone_url="https://oauth2:${gh_token}@github.com/${REPO_URL#https://github.com/}"
    elif [ -z "$gh_token" ] && [ -n "${MITE_TOKEN:-}" ]; then
        gh_token="$MITE_TOKEN"
        info "Using MITE_TOKEN for authentication..."
        clone_url="https://oauth2:${gh_token}@github.com/${REPO_URL#https://github.com/}"
    fi

    # Attempt the clone
    info "Cloning repository..."
    if git clone --depth 1 --branch "$BRANCH" "$clone_url" "$tmp_dir" 2>/tmp/mite_clone_err; then
        ok "Cloned to $tmp_dir"
    else
        local clone_err
        clone_err=$(cat /tmp/mite_clone_err 2>/dev/null)
        rm -f /tmp/mite_clone_err

        err "Clone failed."
        if echo "$clone_err" | grep -qi "authentication\|authorization\|403\|401"; then
            warn "This repo is private. Authenticate via one of:"
            echo "   1. gh auth login  (then re-run update.sh)"
            echo "   2. MITE_TOKEN=ghp_xxx bash update.sh  (personal access token)"
            echo "   3. GITHUB_TOKEN=ghp_xxx bash update.sh"
            echo "   4. Make the repo public at:"
            echo "      https://github.com/AIExperimentsKooth/mite/settings"
        else
            echo "  Error: $clone_err"
            warn "Check internet connection and repo URL: $REPO_URL"
        fi
        return 1
    fi

    # If the install dir doesn't exist, create it
    mkdir -p "$target"

    # Copy everything except .git
    info "Copying files to $target..."
    rsync -a --delete --exclude='.git' "$tmp_dir/" "$target/"
    ok "Files copied to $target"

    # Clean up temp clone
    rm -rf "$tmp_dir"
    return 0
}

if $FRESH_INSTALL; then
    fetch_via_clone "$INSTALL_DIR"
else
    if ! fetch_via_git "$INSTALL_DIR"; then
        info "No git remote found at $INSTALL_DIR \u2014 cloning fresh..."
        fetch_via_clone "$INSTALL_DIR"
    fi
fi

# Initialize git remote if missing (so future updates are faster)
if ! $FRESH_INSTALL && ! $DRY_RUN; then
    cd "$INSTALL_DIR"
    if [ ! -d .git ]; then
        info "Initializing git for future updates..."
        git init --quiet
        git remote add origin "$REPO_URL"
        git fetch --quiet --depth 1 origin "$BRANCH"
        git reset --quiet "origin/$BRANCH" 2>/dev/null || true
        ok "Git initialized with remote: $REPO_URL"
    fi
fi

# --- Step 4: Restore userdata ----------------------------------------------
header "Step 4: Restore userdata (~/.mite/)"

if [ -d "$BACKUP_DIR" ] && [ -d "$HOME/.mite" ]; then
    if $DRY_RUN; then
        info "Would restore ~/.mite/ from backup"
    else
        info "Restoring ~/.mite/ from backup (config, AGENT.md, conversations, queue, schedule)..."
        rsync -a "$BACKUP_DIR/" "$HOME/.mite/"
        ok "Userdata restored"
    fi
elif [ -d "$BACKUP_DIR" ]; then
    if $DRY_RUN; then
        info "Would restore ~/.mite/ from $BACKUP_DIR"
    else
        info "Restoring ~/.mite/ from $BACKUP_DIR (config, AGENT.md, conversations, queue, schedule)..."
        cp -r "$BACKUP_DIR" "$HOME/.mite"
        ok "Userdata restored"
    fi
fi

# --- Step 5: Run setup -----------------------------------------------------
header "Step 5: Run setup"

if $DRY_RUN; then
    info "Would run: cd $INSTALL_DIR && bash setup.sh"
else
    if [ -f "$INSTALL_DIR/setup.sh" ]; then
        info "Running setup..."
        cd "$INSTALL_DIR"
        bash setup.sh
        ok "Setup complete"
    else
        warn "No setup.sh found at $INSTALL_DIR \u2014 skipping."
    fi
fi

# --- Step 6: Ensure CLI is in PATH -----------------------------------------
header "Step 6: Ensure 'mite' command"

if $DRY_RUN; then
    info "Would ensure 'mite' is in PATH"
else
    if command -v mite &>/dev/null; then
        ok "'mite' already available at $(command -v mite)"
    else
        # Try to symlink
        TARGET_DIR="$HOME/.local/bin"
        mkdir -p "$TARGET_DIR"
        if [ -f "$INSTALL_DIR/bin/mite" ]; then
            ln -sf "$INSTALL_DIR/bin/mite" "$TARGET_DIR/mite"
            ok "Linked $INSTALL_DIR/bin/mite \u2192 $TARGET_DIR/mite"
        elif [ -f "$INSTALL_DIR/mite/bin/mite" ]; then
            ln -sf "$INSTALL_DIR/mite/bin/mite" "$TARGET_DIR/mite"
            ok "Linked $INSTALL_DIR/mite/bin/mite \u2192 $TARGET_DIR/mite"
        fi

        case ":${PATH}:" in
            *:"${TARGET_DIR}":*) ;;
            *) warn "Add to PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
        esac
    fi
fi

# --- Done ------------------------------------------------------------------
echo
if $DRY_RUN; then
    warn "[DRY RUN] No changes were made."
else
    ok "${BOLD}Mite is up to date!${NC}"
    echo "  Run:  mite"
    echo "  Or:   cd $INSTALL_DIR && python -m mite"

    # Clean up backup if everything went well
    if [ -d "$BACKUP_DIR" ] && [ -d "$HOME/.mite" ]; then
        info "Backup preserved at: $BACKUP_DIR"
        info "Remove with: rm -rf $BACKUP_DIR"
    fi
fi
echo
