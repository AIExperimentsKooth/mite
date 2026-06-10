#!/usr/bin/env bash
# Mite setup script — installs backend (Ollama or llama.cpp), creates venv, pulls model
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  Mite Setup"
echo "  =========="
echo ""

# Parse arguments
BACKEND="auto"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend) BACKEND="$2"; shift 2 ;;
        --yes|-y) YES=1; shift ;;
        *) echo "  Unknown option: $1"; exit 1 ;;
    esac
done

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "  Python 3 is required but not found."
    echo "  Install Python 3.10+ and try again."
    exit 1
fi

# Detect architecture
ARCH=$(python3 -c "import platform; print(platform.machine())")
echo "  Architecture: $ARCH"

if [ "$BACKEND" = "auto" ]; then
    case "$ARCH" in
        i386|i486|i586|i686|armv6*|armv7*)
            BACKEND="llamacpp"
            echo "  32-bit architecture detected — using llama.cpp backend"
            ;;
        *)
            BACKEND="ollama"
            ;;
    esac
fi
echo "  Backend: $BACKEND"
echo ""

# Create venv if needed
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "  Creating Python virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

# Activate venv for pip operations
source "$SCRIPT_DIR/.venv/bin/activate" 2>/dev/null || true

# Create bin/mite wrapper
mkdir -p "$SCRIPT_DIR/bin"
cat > "$SCRIPT_DIR/bin/mite" << 'EOF'
#!/usr/bin/env python3
"""Mite - run without -m."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mite.cli import main
main()
EOF
chmod +x "$SCRIPT_DIR/bin/mite"

if [ "$BACKEND" = "llamacpp" ]; then
    echo "  Setting up llama.cpp backend..."
    if ! python3 -m mite.setup.run 2>/dev/null; then
        python3 -c "
from mite.setup import run
run('qwen2.5:0.5b', backend='llamacpp')
"
    fi
    echo ""
    echo "  Setup complete! Run: python -m mite --backend llamacpp"
else
    # Ollama path
    if ! command -v ollama &>/dev/null; then
        echo "  Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi

    echo "  Starting Ollama server..."
    ollama serve &>/dev/null &
    sleep 3

    echo "  Pulling default model..."
    python3 -m mite.setup 2>/dev/null || python3 -c "
from mite.setup import run
run('qwen2.5:0.5b', backend='ollama')
"

    echo ""
    echo "  Setup complete! Run: python -m mite"
fi
echo ""
