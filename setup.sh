#!/usr/bin/env bash
# Mite setup script — installs Ollama, sets up Python venv, pulls model
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  Mite Setup"
echo "  =========="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "  Python 3 is required but not found."
    echo "  Install Python 3.10+ and try again."
    exit 1
fi

# Create venv if needed
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "  Creating Python virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

# Activate venv for pip operations
source "$SCRIPT_DIR/.venv/bin/activate"

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

# Check if Ollama is installed
if ! command -v ollama &>/dev/null; then
    echo "  Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

echo "  Starting Ollama server..."
ollama serve &>/dev/null &
OLLAMA_PID=$!
sleep 3

echo "  Pulling default model..."
python3 -m mite --setup --yes

echo ""
echo "  Setup complete! Run: python -m mite"
echo ""
