import subprocess
import sys
import os
import time
import urllib.request
import urllib.error
import json
import shutil
import platform


# ---------------------------------------------------------------------------
# Architecture detection
# ---------------------------------------------------------------------------

def detect_arch() -> str:
    """Detect the CPU architecture for backend selection."""
    machine = platform.machine().lower()
    if machine in ("i386", "i486", "i586", "i686"):
        return "i686"
    elif machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("aarch64", "arm64"):
        return "aarch64"
    elif machine.startswith("armv"):
        return "arm"
    return machine


def is_32bit() -> bool:
    """Check if running on a 32-bit architecture that can't run Ollama."""
    arch = detect_arch()
    return arch in ("i686", "armv6", "armv7", "arm")


def suggest_backend() -> str:
    """Suggest which backend to use based on architecture."""
    if is_32bit():
        return "llamacpp"
    return "ollama"


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

def check_ollama() -> bool:
    """Check if Ollama is installed."""
    return shutil.which("ollama") is not None


def install_ollama():
    """Install Ollama using the official script."""
    if is_32bit():
        print("  \u26a0 Ollama does not support 32-bit architectures.")
        print("  Use llama.cpp backend instead: mite --backend llamacpp")
        return False

    print("\n  \u23f3 Installing Ollama...")
    if shutil.which("apt-get"):
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
    if check_ollama():
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            urllib.request.urlopen(req, timeout=3)
            return True
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
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
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            urllib.request.urlopen(req, timeout=3)
            return True
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
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


def test_ollama(model: str):
    """Quick test: verify Ollama model responds."""
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


# ---------------------------------------------------------------------------
# llama.cpp backend
# ---------------------------------------------------------------------------

def _llamacpp_dir() -> str:
    """Get the llama.cpp installation directory under ~/.mite/."""
    d = os.path.join(os.path.expanduser("~"), ".mite", "llamacpp")
    os.makedirs(d, exist_ok=True)
    return d


def _llama_server_path() -> str:
    """Path to the llama-server binary."""
    return os.path.join(_llamacpp_dir(), "build", "bin", "llama-server")


def _models_dir() -> str:
    """Directory where GGUF models are stored."""
    d = os.path.join(_llamacpp_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def check_llamacpp() -> bool:
    """Check if llama.cpp server binary exists."""
    return os.path.isfile(_llama_server_path())


def check_build_tools() -> bool:
    """Check if cmake, make, and a C++ compiler are available."""
    cmake = shutil.which("cmake")
    make = shutil.which("make")
    cc = shutil.which("g++") or shutil.which("c++") or shutil.which("clang++")
    missing = []
    if not cmake:
        missing.append("cmake")
    if not make:
        missing.append("make")
    if not cc:
        missing.append("C++ compiler (g++/clang++)")
    if missing:
        print(f"  \u26a0 Missing build tools: {', '.join(missing)}")
        print("  Install them with your package manager, e.g.:")
        print("    apt-get install build-essential cmake")
        return False
    return True


def install_build_tools():
    """Try to install build tools via package manager."""
    print("  \u23f3 Installing build tools...")
    if shutil.which("apt-get"):
        result = subprocess.run(
            ["apt-get", "update", "-qq"],
            capture_output=True, text=True, timeout=120
        )
        result = subprocess.run(
            ["apt-get", "install", "-y", "-qq", "build-essential", "cmake"],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            print("  \u2705 Build tools installed")
            return True
        print(f"  \u26a0 Failed to install: {result.stderr[:200]}")
        return False
    else:
        print("  \u26a0 Please install build-essential and cmake manually")
        return False


def install_llamacpp():
    """Build llama.cpp from source for the current architecture.

    This is the only reliable way to get llama.cpp working on i686 and
    other architectures that don't have pre-built binaries.
    """
    dest = _llamacpp_dir()
    server_path = _llama_server_path()

    if check_llamacpp():
        print("  \u2705 llama.cpp server already built")
        return True

    # Ensure build tools
    if not check_build_tools():
        if not install_build_tools():
            return False

    print("  \u23f3 Cloning llama.cpp (shallow)...")
    repo_dir = os.path.join(dest, "source")
    if not os.path.isdir(repo_dir):
        result = subprocess.run(
            ["git", "clone", "--depth=1",
             "https://github.com/ggerganov/llama.cpp",
             repo_dir],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"  \u26a0 Clone failed: {result.stderr[:200]}")
            return False
        print("  \u2705 Repository cloned")
    else:
        print("  \u2014 Repository already exists, updating...")
        subprocess.run(
            ["git", "-C", repo_dir, "pull"],
            capture_output=True, text=True, timeout=30
        )

    print("  \u23f3 Building llama.cpp (this may take a while on slower devices)...")
    build_dir = os.path.join(dest, "build")
    os.makedirs(build_dir, exist_ok=True)

    # Disable GPU acceleration — we want maximum portability
    cmake_args = [
        "cmake", repo_dir,
        "-B", build_dir,
        "-DLLAMA_CUDA=OFF",
        "-DLLAMA_METAL=OFF",
        "-DLLAMA_VULKAN=OFF",
        "-DLLAMA_OPENBLAS=OFF",
        "-DLLAMA_LLAMAFILE=OFF",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    # On i686 / 32-bit ARM, add flags for better performance
    arch = detect_arch()
    if arch == "i686":
        cmake_args.append("-DLLAMA_NATIVE=OFF")

    result = subprocess.run(cmake_args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"  \u26a0 CMake configure failed: {result.stderr[:300]}")
        return False

    # Build only the server (faster than everything)
    cpu_count = os.cpu_count() or 1
    print(f"  \u23f3 Building llama.cpp server (-j {cpu_count})...")
    print(f"    (this can take 10-60+ minutes on slower devices)")
    build_cmd = ["cmake", "--build", build_dir, "--target", "llama-server",
                  "-j", str(cpu_count)]
    build_proc = subprocess.Popen(
        build_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )
    last_line = ""
    if build_proc.stdout:
        for line in build_proc.stdout:
            line = line.rstrip()
            if line:
                last_line = line
                # Print a compact progress indicator (last 80 chars of each relevant line)
                short = line.strip()[-80:] if len(line.strip()) > 80 else line.strip()
                print(f"    {short}")
    build_proc.wait()
    if build_proc.returncode != 0:
        print(f"  \u26a0 Build failed (exit code {build_proc.returncode})")
        print(f"  Last output: {last_line[:200]}")
        return False

    if check_llamacpp():
        print(f"  \u2705 llama.cpp server built ({arch})")
        return True

    print("  \u26a0 Build completed but server binary not found")
    return False


def resolve_gguf_model(model_spec: str) -> str:
    """Convert a model name like 'qwen2.5:0.5b' to a GGUF file path.

    Downloads from HuggingFace if not cached.
    Returns the absolute path to the GGUF file.
    """
    models_dir = _models_dir()

    # Normalize model spec: qwen2.5:0.5b -> qwen2.5-0.5b
    safe_name = model_spec.replace(":", "-").replace("/", "--")
    model_path = os.path.join(models_dir, f"{safe_name}.gguf")

    if os.path.isfile(model_path):
        return model_path

    # Map common model specs to HuggingFace repos
    hf_map = {
        "qwen2.5:0.5b": ("Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                          "qwen2.5-0.5b-instruct-q4_k_m.gguf"),
        "qwen2.5:1.5b": ("Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                         "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
        "qwen2.5:0.5b-instruct": ("Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                                  "qwen2.5-0.5b-instruct-q4_k_m.gguf"),
        "qwen2.5:0.8b": ("Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                         "qwen2.5-0.5b-instruct-q4_k_m.gguf"),
        "llama3.2:1b": ("huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF",
                        "Llama-3.2-1B-Instruct-Q4_K_M.gguf"),
        "llama3.2:3b": ("huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF",
                        "Llama-3.2-3B-Instruct-Q4_K_M.gguf"),
    }

    entry = hf_map.get(model_spec)
    if not entry:
        # Try to find a match by prefix
        for key, val in hf_map.items():
            if model_spec.startswith(key.split(":")[0]):
                entry = val
                break

    if entry:
        repo, filename = entry
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        print(f"  \u23f3 Downloading {filename} ({_approx_size_str(filename)})...")
        print(f"    From: {repo}")
        try:
            urllib.request.urlretrieve(url, model_path)
            print(f"  \u2705 Model cached: {model_path}")
            return model_path
        except Exception as e:
            print(f"  \u26a0 Download failed: {e}")
            print("  Download manually and place in ~/.mite/llamacpp/models/")
            return model_spec  # fall back to using the spec name as-is

    print(f"  \u26a0 Unknown model: {model_spec}")
    print("  Download a GGUF file manually to ~/.mite/llamacpp/models/")
    return model_spec


def _approx_size_str(filename: str) -> str:
    """Return an approximate download size hint based on filename."""
    if "0.5b" in filename.lower():
        return "~350 MB"
    if "1b" in filename.lower() or "1.5b" in filename.lower():
        return "~1 GB"
    if "3b" in filename.lower():
        return "~2 GB"
    return "large file"


def start_llamacpp(model: str, host: str = "http://localhost:8080"):
    """Start the llama.cpp server with the given model."""
    import urllib.error

    # Check if already running
    try:
        req = urllib.request.Request(f"{host}/v1/models")
        urllib.request.urlopen(req, timeout=3)
        return True
    except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
        pass

    server_path = _llama_server_path()
    if not os.path.isfile(server_path):
        print("  \u26a0 llama.cpp server not built. Run setup first.")
        return False

    # Resolve model path
    model_path = resolve_gguf_model(model)
    if not os.path.isfile(model_path):
        print(f"  \u26a0 Model file not found: {model_path}")
        print("  Download a GGUF file to ~/.mite/llamacpp/models/")
        return False

    print(f"  \u23f3 Starting llama.cpp server (model: {os.path.basename(model_path)})...")
    log_path = os.path.join(_llamacpp_dir(), "server.log")
    try:
        with open(log_path, "w") as log:
            subprocess.Popen(
                [server_path,
                 "-m", model_path,
                 "--host", "0.0.0.0",
                 "--port", "8080",
                 "-c", "4096",     # context size
                 "--mlock",        # lock memory to avoid swapping
                 "-ngl", "0",      # no GPU layers (pure CPU)
                 ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        time.sleep(3)
        return True
    except Exception as e:
        print(f"  \u26a0 Failed to start llama.cpp: {e}")
        return False


def wait_for_llamacpp(max_wait: int = 60, host: str = "http://localhost:8080"):
    """Wait for llama.cpp server to become available."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{host}/v1/models")
            urllib.request.urlopen(req, timeout=3)
            print(f"  \u2705 llama.cpp server ready on {host}")
            return True
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
            time.sleep(2)
    print(f"  \u26a0 llama.cpp not ready after {max_wait}s")
    print(f"  Check log: {os.path.join(_llamacpp_dir(), 'server.log')}")
    return False


def test_llamacpp(host: str = "http://localhost:8080"):
    """Quick test: verify llama.cpp responds."""
    print("  \u23f3 Testing llama.cpp...")
    try:
        payload = json.dumps({
            "model": "default",
            "messages": [{"role": "user", "content": "say hi"}],
            "stream": False,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            f"{host}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if reply:
                print(f"  \u2705 llama.cpp responds: {reply[:60]}...")
                return True
    except Exception as e:
        print(f"  \u26a0 Test failed: {e}")
    return False


# ---------------------------------------------------------------------------
# Unified setup
# ---------------------------------------------------------------------------

def run(model: str, backend: str = "auto"):
    """Full setup: install backend, start server, pull model, test.

    Args:
        model: Model name/spec string.
        backend: "ollama", "llamacpp", or "auto" (auto-detect).
    """
    if backend == "auto":
        backend = suggest_backend()
    print(f"\n  \u2699  Backend: {backend}")

    if backend == "llamacpp":
        if not check_llamacpp():
            if not install_llamacpp():
                sys.exit(1)
        else:
            print("  \u2705 llama.cpp already installed")
        if not start_llamacpp(model):
            sys.exit(1)
        if not wait_for_llamacpp():
            sys.exit(1)
        test_llamacpp()
        print(f"\n  \u2705 Setup complete! Using llama.cpp backend (model: {model}).\n")
        print("  Run: mite --backend llamacpp")
    else:
        if not check_ollama():
            if not install_ollama():
                sys.exit(1)
        if not start_ollama():
            sys.exit(1)
        if not wait_for_ollama():
            sys.exit(1)
        if not pull_model(model):
            sys.exit(1)
        test_ollama(model)
        print(f"\n  \u2705 Setup complete! Model '{model}' is ready.\n")
