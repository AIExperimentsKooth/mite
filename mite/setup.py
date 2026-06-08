import subprocess
import sys
import os
import time
import urllib.request
import json


def check_ollama() -> bool:
    """Check if Ollama is installed."""
    import shutil
    return shutil.which("ollama") is not None


def install_ollama():
    """Install Ollama using the official script."""
    import shutil
    print("\n  \u23f3 Installing Ollama...")
    # Check for common package managers
    if shutil.which("apt-get"):
        # Debian/Ubuntu - use official script
        result = subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            print(f"  \u26a0 Ollama install failed: {result.stderr[:300]}")
            print("  Install manually: https://ollama.com/download")
            return False
        print("  \u2705 Ollama installed")
        return True
    else:
        print("  \u26a0 Unsupported package manager. Install Ollama manually:")
        print("  https://ollama.com/download")
        return False


def start_ollama():
    """Start the Ollama server if not running."""
    import urllib.error
    # Check if already running
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        urllib.request.urlopen(req, timeout=3)
        # Server is running
        return True
    except (urllib.error.URLError, ConnectionRefusedError):
        pass

    print("  \u23f3 Starting Ollama server...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        return True
    except Exception as e:
        print(f"  \u26a0 Failed to start Ollama: {e}")
        print("  Start manually: ollama serve")
        return False


def wait_for_ollama(max_wait: int = 30):
    """Wait for Ollama server to become available."""
    import urllib.error
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            urllib.request.urlopen(req, timeout=3)
            return True
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(1)
    print(f"  \u26a0 Ollama not ready after {max_wait}s")
    return False


def pull_model(model: str):
    """Pull a model from Ollama."""
    print(f"  \u23f3 Pulling model '{model}' (this may take a while)...")
    result = subprocess.run(
        ["ollama", "pull", model],
        capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        print(f"  \u26a0 Pull failed: {result.stderr[:300]}")
        return False
    print(f"  \u2705 Model '{model}' ready")
    return True


def test_model(model: str):
    """Quick test: verify the model responds."""
    print(f"  \u23f3 Testing model...")
    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "say hi"}],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            reply = data.get("message", {}).get("content", "")
            if reply:
                print(f"  \u2705 Model responds: {reply[:60]}...")
                return True
    except Exception as e:
        print(f"  \u26a0 Test failed: {e}")
    return False


def run(model: str):
    """Full setup: install, start, pull, test."""
    if not check_ollama():
        if not install_ollama():
            sys.exit(1)
    if not start_ollama():
        sys.exit(1)
    if not wait_for_ollama():
        sys.exit(1)
    if not pull_model(model):
        sys.exit(1)
    test_model(model)
    print(f"\n  \u2705 Setup complete! Model '{model}' is ready.\n")
