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


def verify_ollama(model: str):
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
# llama.cpp backend (via llama-cpp-python[server])
# ---------------------------------------------------------------------------

def _llamacpp_dir() -> str:
    """Get the llama.cpp data directory under [mite-root]/llamacpp/."""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llamacpp")
    os.makedirs(d, exist_ok=True)
    return d


def _models_dir() -> str:
    """Directory where GGUF models are stored."""
    d = os.path.join(_llamacpp_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def check_llamacpp() -> bool:
    """Check if llama-cpp-python is installed."""
    import importlib.util
    return importlib.util.find_spec("llama_cpp") is not None


def install_llamacpp_python():
    """Install llama-cpp-python[server] via pip."""
    print("  \u23f3 Installing llama-cpp-python[server] via pip...")
    print("    (this may compile from source on older architectures,")
    print("     but pip handles caching and retries automatically)")
    python = sys.executable
    result = subprocess.run(
        [python, "-m", "pip", "install", "llama-cpp-python[server]"],
        capture_output=True, text=True, timeout=900  # up to 15m for compilation
    )
    if result.returncode != 0:
        err = result.stderr.strip()[-400:]
        print(f"  \u26a0 pip install failed: {err}")
        print("  Try installing manually:")
        print(f"    {python} -m pip install llama-cpp-python[server]")
        return False
    if not check_llamacpp():
        print("  \u26a0 Installed but package not importable — check for errors above")
        return False
    print("  \u2705 llama-cpp-python[server] installed")
    return True


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
            print("  Download manually and place in [mite-root]/llamacpp/models/")
            return model_spec  # fall back to using the spec name as-is

    print(f"  \u26a0 Unknown model: {model_spec}")
    print("  Download a GGUF file manually to [mite-root]/llamacpp/models/")
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


def start_llamacpp(model: str, host: str = "0.0.0.0", port: int = 8080):
    """Start the llama-cpp-python server with the given model."""
    import urllib.error

    server_url = f"http://{host}:{port}"

    # Check if already running
    try:
        req = urllib.request.Request(f"{server_url}/v1/models")
        urllib.request.urlopen(req, timeout=3)
        return True
    except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
        pass

    if not check_llamacpp():
        print("  \u26a0 llama-cpp-python not installed. Run setup first.")
        return False

    # Resolve model path
    model_path = resolve_gguf_model(model)
    if not os.path.isfile(model_path):
        print(f"  \u26a0 Model file not found: {model_path}")
        print("  Download a GGUF file to [mite-root]/llamacpp/models/")
        return False

    print(f"  \u23f3 Starting llama.cpp server (model: {os.path.basename(model_path)})...")
    log_path = os.path.join(_llamacpp_dir(), "server.log")
    try:
        with open(log_path, "w") as log:
            subprocess.Popen(
                [sys.executable, "-m", "llama_cpp.server",
                 "--model", model_path,
                 "--host", host,
                 "--port", str(port),
                 "--n_ctx", "4096",
                 "--n_gpu_layers", "0",
                 ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    except Exception as e:
        print(f"  \u26a0 Failed to start llama.cpp server: {e}")
        return False
    # Poll until ready (up to 120s — slow devices need time)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{server_url}/v1/models")
            urllib.request.urlopen(req, timeout=3)
            print(f"  \u2705 llama.cpp server started on {server_url}")
            return True
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
            time.sleep(2)
    print(f"  \u26a0 llama.cpp server not ready after 120s")
    print(f"  Check log: {log_path}")
    return False



def wait_for_llamacpp(max_wait: int = 60, host: str = "0.0.0.0", port: int = 8080):
    """Wait for llama.cpp server to become available."""
    server_url = f"http://{host}:{port}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{server_url}/v1/models")
            urllib.request.urlopen(req, timeout=3)
            print(f"  \u2705 llama.cpp server ready on {server_url}")
            return True
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionError):
            time.sleep(2)
    print(f"  \u26a0 llama.cpp not ready after {max_wait}s")
    print(f"  Check log: {os.path.join(_llamacpp_dir(), 'server.log')}")
    return False


def check_llamacpp_endpoint(host: str = "0.0.0.0", port: int = 8080) -> bool:
    """Check if a llama.cpp endpoint is reachable (for external endpoints)."""
    import urllib.error
    server_url = f"http://{host}:{port}"
    try:
        req = urllib.request.Request(f"{server_url}/v1/models")
        urllib.request.urlopen(req, timeout=5)
        return True
    except (urllib.error.URLError, ConnectionRefusedError, ConnectionError, OSError):
        return False


# ---------------------------------------------------------------------------
# Backend discovery — find running backends and their model lists
# ---------------------------------------------------------------------------

def _probe_ollama_models() -> list | None:
    """Fetch Ollama model list from localhost:11434. Returns list of model names or None."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        models = data.get("models", [])
        names = [m.get("name", "") for m in models if m.get("name")]
        return names if names else None
    except Exception:
        return None


def _probe_openai_models(host: str, port: int) -> list | None:
    """Fetch model list from an OpenAI-compatible /v1/models endpoint. Returns list or None."""
    try:
        url = f"http://{host}:{port}/v1/models"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        models = data.get("data", [])
        names = [m.get("id", "") for m in models if m.get("id")]
        return names if names else None
    except Exception:
        return None


# Common ports for OpenAI-compatible servers (LLMStudio, llama.cpp, etc.)
_OPENAI_PORTS = [
    (1234, "LLMStudio / LM Studio"),
    (8080, "llama.cpp"),
    (8000, "Generic OpenAI"),
    (11434, "Ollama (OpenAI compat)"),
    (5000, "Generic"),
    (3000, "Generic"),
    (8081, "llama.cpp (alt)"),
]


def discover_backends(custom_hosts: list[str] | None = None) -> list[dict]:
    """Scan for accessible LLM backends and return their model lists.

    Returns a list of dicts:
        [
            {
                "backend": "ollama",
                "host": "http://localhost:11434",
                "models": ["qwen2.5:0.5b", ...]
            },
            {
                "backend": "llamacpp",
                "host": "http://localhost:1234",
                "models": ["model-id", ...]
            },
        ]

    Checks Ollama's native API, common OpenAI-compatible ports,
    and any custom hosts the user has configured.
    """
    found: list[dict] = []
    _found_hosts: set[str] = set()  # track already-discovered hosts to avoid duplicates

    # 1) Probe Ollama (native API)
    ollama_models = _probe_ollama_models()
    if ollama_models:
        found.append({
            "backend": "ollama",
            "host": "http://localhost:11434",
            "models": ollama_models,
        })
        _found_hosts.add("127.0.0.1:11434")
        _found_hosts.add("localhost:11434")

    # 2) Probe common OpenAI-compatible ports
    probed_ports = set()
    for port, label in _OPENAI_PORTS:
        if port in probed_ports:
            continue
        probed_ports.add(port)
        # Skip ports already covered by native discovery
        host_key = f"127.0.0.1:{port}"
        if host_key in _found_hosts:
            continue
        models = _probe_openai_models("127.0.0.1", port)
        if models:
            found.append({
                "backend": "llamacpp",
                "host": f"http://127.0.0.1:{port}",
                "models": models,
            })
            _found_hosts.add(host_key)

    # 3) Probe custom hosts from config
    if custom_hosts:
        for entry in custom_hosts:
            # entry format: "backend_name|http://host:port" or just "http://host:port"
            if "|" in entry:
                parts = entry.split("|", 1)
                host_url = parts[1]
            else:
                host_url = entry
            # Parse hostname and port from the URL
            from urllib.parse import urlparse
            parsed = urlparse(host_url)
            if parsed.hostname and parsed.port:
                models = _probe_openai_models(parsed.hostname, parsed.port)
                if models:
                    found.append({
                        "backend": "llamacpp",
                        "host": host_url,
                        "models": models,
                    })

    return found


def verify_llamacpp(host: str = "0.0.0.0", port: int = 8080):
    """Quick test: verify llama.cpp server responds."""
    server_url = f"http://{host}:{port}"
    print("  \u23f3 Testing llama.cpp...")
    try:
        payload = json.dumps({
            "model": "default",
            "messages": [{"role": "user", "content": "say hi"}],
            "stream": False,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            f"{server_url}/v1/chat/completions",
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

def run(model: str, backend: str = "auto", host: str = "0.0.0.0", port: int = 8080):
    """Full setup: install backend, start server, pull model, test.

    Args:
        model: Model name/spec string.
        backend: "ollama", "llamacpp", or "auto" (auto-detect).
        host: Bind host for the llama.cpp server (default: "0.0.0.0").
        port: Port for the llama.cpp server (default: 8080).
    """
    if backend == "auto":
        backend = suggest_backend()
    print(f"\n  \u2699  Backend: {backend}")

    if backend == "llamacpp":
        if not check_llamacpp():
            if not install_llamacpp_python():
                sys.exit(1)
        else:
            print("  \u2705 llama-cpp-python already installed")
        if not start_llamacpp(model, host=host, port=port):
            sys.exit(1)
        if not wait_for_llamacpp(host=host, port=port):
            sys.exit(1)
        verify_llamacpp(host=host, port=port)
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
        verify_ollama(model)
        print(f"\n  \u2705 Setup complete! Model '{model}' is ready.\n")
