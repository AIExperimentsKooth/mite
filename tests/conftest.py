"""Shared fixtures for Mite backend tests."""

import json
import time
import subprocess
import urllib.request
import urllib.error
import os
import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ollama_alive(host="http://localhost:11434") -> bool:
    """Check if an Ollama server is reachable."""
    try:
        req = urllib.request.Request(f"{host}/api/tags")
        urllib.request.urlopen(req, timeout=3)
        return True
    except (urllib.error.URLError, ConnectionRefusedError, ConnectionError, OSError):
        return False


def _llamacpp_alive(host="http://localhost:8080") -> bool:
    """Check if a llama.cpp server is reachable."""
    try:
        req = urllib.request.Request(f"{host}/v1/models")
        urllib.request.urlopen(req, timeout=3)
        return True
    except (urllib.error.URLError, ConnectionRefusedError, ConnectionError, OSError):
        return False


def _has_ollama_model(model="qwen2.5:0.5b") -> bool:
    """Check if model is available in Ollama."""
    try:
        req = urllib.request.Request(f"http://localhost:11434/api/tags")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        models = data.get("models", [])
        names = [m.get("name", "") for m in models]
        return any(model in n for n in names)
    except Exception:
        return False


def _llamacpp_importable() -> bool:
    """Check if llama-cpp-python is installed."""
    import importlib.util
    return importlib.util.find_spec("llama_cpp") is not None


def _has_gguf_model() -> bool:
    """Check if any GGUF file exists in ~/.mite/llamacpp/models/."""
    gguf_dir = os.path.expanduser("~/.mite/llamacpp/models")
    if not os.path.isdir(gguf_dir):
        return False
    for f in os.listdir(gguf_dir):
        if f.endswith(".gguf"):
            return True
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ollama_url():
    """Return the default Ollama URL."""
    return "http://localhost:11434"


@pytest.fixture
def llamacpp_url():
    """Return the default llama.cpp URL."""
    return "http://localhost:8080"


@pytest.fixture
def sample_messages():
    """Return a minimal message list for testing."""
    return [{"role": "user", "content": "say hi"}]


@pytest.fixture
def mock_ollama_response():
    """Simulate a valid Ollama API response."""
    return json.dumps({
        "message": {"content": "Hi there!"},
        "done": True
    }).encode()


@pytest.fixture
def mock_llamacpp_response():
    """Simulate a valid llama.cpp (OpenAI-compatible) API response."""
    return json.dumps({
        "choices": [{"message": {"content": "Hi there!"}}]
    }).encode()


# ---------------------------------------------------------------------------
# Integration skip markers
# ---------------------------------------------------------------------------

ollama_available = pytest.mark.skipif(
    not _ollama_alive(),
    reason="Ollama server is not running on localhost:11434"
)

ollama_model_available = pytest.mark.skipif(
    not _has_ollama_model(),
    reason="No Ollama model pulled (need qwen2.5:0.5b)"
)

llamacpp_running = pytest.mark.skipif(
    not _llamacpp_alive(),
    reason="llama.cpp server is not running on localhost:8080"
)

llamacpp_installed = pytest.mark.skipif(
    not _llamacpp_importable(),
    reason="llama-cpp-python is not installed"
)

llamacpp_model_available = pytest.mark.skipif(
    not _has_gguf_model(),
    reason="No GGUF model file in ~/.mite/llamacpp/models/"
)
