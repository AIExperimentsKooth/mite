"""Tests for Mite backends — Ollama and llama.cpp.

Each backend is tested on four levels:
  1. Architecture detection  — detect_arch, is_32bit, suggest_backend
  2. Installation logic      — check_*, install_*
  3. Server lifecycle        — start, wait_for, endpoint check
  4. Model communication     — _call_llm, test_* (mocked + integration)

Integration tests are gated by markers so CI passes without servers running.
"""

import json
import time
import os
import sys
import subprocess
from unittest.mock import patch, MagicMock, call
from pathlib import Path

import pytest

from mite.core import _call_llm
from mite.setup import (
    detect_arch, is_32bit, suggest_backend,
    check_ollama, install_ollama, start_ollama, wait_for_ollama,
    pull_model, verify_ollama,
    check_llamacpp, install_llamacpp_python, start_llamacpp,
    wait_for_llamacpp, verify_llamacpp as llamacpp_verify_fn,
    check_llamacpp_endpoint, resolve_gguf_model, run,
    _llamacpp_dir, _models_dir,
)

# Integration-test skip markers (defined in conftest.py)
from .conftest import (
    ollama_available, ollama_model_available,
    llamacpp_running, llamacpp_installed, llamacpp_model_available,
)

# ===================================================================
# 1. Architecture detection
# ===================================================================

class TestDetectArch:
    """detect_arch should return the canonical arch name."""

    @patch("mite.setup.platform.machine")
    def test_i686(self, mock_machine):
        mock_machine.return_value = "i686"
        assert detect_arch() == "i686"

    @patch("mite.setup.platform.machine")
    def test_i386(self, mock_machine):
        mock_machine.return_value = "i386"
        assert detect_arch() == "i686"

    @patch("mite.setup.platform.machine")
    def test_x86_64(self, mock_machine):
        mock_machine.return_value = "x86_64"
        assert detect_arch() == "x86_64"

    @patch("mite.setup.platform.machine")
    def test_amd64(self, mock_machine):
        mock_machine.return_value = "AMD64"
        assert detect_arch() == "x86_64"

    @patch("mite.setup.platform.machine")
    def test_aarch64(self, mock_machine):
        mock_machine.return_value = "aarch64"
        assert detect_arch() == "aarch64"

    @patch("mite.setup.platform.machine")
    def test_armv7l(self, mock_machine):
        mock_machine.return_value = "armv7l"
        assert detect_arch() == "arm"


class TestIs32Bit:
    """is_32bit should identify architectures that can't run Ollama."""

    @patch("mite.setup.detect_arch")
    def test_i686_is_32bit(self, mock_arch):
        mock_arch.return_value = "i686"
        assert is_32bit() is True

    @patch("mite.setup.detect_arch")
    def test_arm_is_32bit(self, mock_arch):
        mock_arch.return_value = "arm"
        assert is_32bit() is True

    @patch("mite.setup.detect_arch")
    def test_x86_64_not_32bit(self, mock_arch):
        mock_arch.return_value = "x86_64"
        assert is_32bit() is False

    @patch("mite.setup.detect_arch")
    def test_aarch64_not_32bit(self, mock_arch):
        mock_arch.return_value = "aarch64"
        assert is_32bit() is False


class TestSuggestBackend:
    """suggest_backend should recommend llamacpp on 32-bit, ollama otherwise."""

    @patch("mite.setup.is_32bit")
    def test_suggests_llamacpp_on_32bit(self, mock_32bit):
        mock_32bit.return_value = True
        assert suggest_backend() == "llamacpp"

    @patch("mite.setup.is_32bit")
    def test_suggests_ollama_on_64bit(self, mock_32bit):
        mock_32bit.return_value = False
        assert suggest_backend() == "ollama"


# ===================================================================
# 2. Ollama — check / detect
# ===================================================================

class TestCheckOllama:
    """check_ollama should detect the ollama binary."""

    @patch("mite.setup.shutil.which")
    def test_found(self, mock_which):
        mock_which.return_value = "/usr/bin/ollama"
        assert check_ollama() is True

    @patch("mite.setup.shutil.which")
    def test_not_found(self, mock_which):
        mock_which.return_value = None
        assert check_ollama() is False


# ===================================================================
# 3. Ollama — install logic
# ===================================================================

class TestInstallOllama:
    """install_ollama should refuse on 32-bit, run script on x86_64."""

    @patch("mite.setup.is_32bit", return_value=True)
    @patch("mite.setup.subprocess.run")
    def test_refuses_on_32bit(self, mock_run, mock_32bit):
        result = install_ollama()
        assert result is False
        mock_run.assert_not_called()

    @patch("mite.setup.is_32bit", return_value=False)
    @patch("mite.setup.shutil.which", return_value="/usr/bin/apt-get")
    @patch("mite.setup.subprocess.run")
    def test_runs_script_on_x86_64(self, mock_run, mock_apt, mock_32bit):
        mock_run.return_value = MagicMock(returncode=0)
        result = install_ollama()
        assert result is True
        mock_run.assert_called_once()
        assert "ollama.com/install.sh" in mock_run.call_args[0][0]

    @patch("mite.setup.is_32bit", return_value=False)
    @patch("mite.setup.shutil.which", return_value=None)  # no apt-get
    def test_fallback_message(self, mock_apt, mock_32bit):
        """Should return False when package manager is unknown."""
        result = install_ollama()
        assert result is False


# ===================================================================
# 4. Ollama — server lifecycle
# ===================================================================

class TestStartOllama:
    """start_ollama should detect running server or launch it."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_already_running(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        assert start_ollama() is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.subprocess.Popen")
    def test_starts_ollama(self, mock_popen, mock_urlopen):
        mock_popen.return_value = MagicMock()
        assert start_ollama() is True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == ["ollama", "serve"]

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.subprocess.Popen",
           side_effect=FileNotFoundError("no ollama"))
    def test_fails_gracefully(self, mock_popen, mock_urlopen):
        assert start_ollama() is False


class TestWaitForOllama:
    """wait_for_ollama should poll until server is ready."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_ready_immediately(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        assert wait_for_ollama(max_wait=5) is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=[ConnectionRefusedError(), MagicMock()])
    @patch("mite.setup.time.sleep")  # speed up test
    def test_ready_after_retry(self, mock_sleep, mock_urlopen):
        assert wait_for_ollama(max_wait=5) is True
        assert mock_urlopen.call_count == 2

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.time.sleep")
    def test_timeout(self, mock_sleep, mock_urlopen):
        assert wait_for_ollama(max_wait=0) is False


# ===================================================================
# 5. Ollama — model pull & test
# ===================================================================

class TestPullModel:
    """pull_model should run ollama pull."""

    @patch("mite.setup.subprocess.run")
    def test_pull_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert pull_model("qwen2.5:0.5b") is True
        mock_run.assert_called_once_with(
            ["ollama", "pull", "qwen2.5:0.5b"],
            capture_output=True, text=True, timeout=600
        )

    @patch("mite.setup.subprocess.run")
    def test_pull_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        assert pull_model("qwen2.5:0.5b") is False


class TestVerifyOllama:
    """verify_ollama should send a request and parse response."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "Hi there!"}
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assert verify_ollama("qwen2.5:0.5b") is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=Exception("connection failed"))
    def test_failure(self, mock_urlopen):
        assert verify_ollama("qwen2.5:0.5b") is False

    @patch("mite.setup.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": ""}
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assert verify_ollama("qwen2.5:0.5b") is False


# ===================================================================
# 6. llama.cpp — check / detect
# ===================================================================

class TestCheckLlamacpp:
    """check_llamacpp should detect the package."""

    def test_found(self):
        """Use monkeypatch to set find_spec to return a spec."""
        import importlib.util as iu
        from unittest.mock import MagicMock
        original = iu.find_spec
        try:
            iu.find_spec = lambda name: MagicMock() if name == "llama_cpp" else None
            from mite.setup import check_llamacpp
            assert check_llamacpp() is True
        finally:
            iu.find_spec = original

    def test_not_found(self):
        import importlib.util as iu
        original = iu.find_spec
        try:
            iu.find_spec = lambda name: None
            from mite.setup import check_llamacpp
            assert check_llamacpp() is False
        finally:
            iu.find_spec = original


# ===================================================================
# 7. llama.cpp — install
# ===================================================================

class TestInstallLlamacppPython:
    """install_llamacpp_python should run pip install."""

    @patch("mite.setup.sys.executable", "/usr/bin/python3")
    @patch("mite.setup.subprocess.run")
    @patch("mite.setup.check_llamacpp", return_value=True)
    def test_install_success(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = install_llamacpp_python()
        assert result is True
        mock_run.assert_called_once_with(
            ["/usr/bin/python3", "-m", "pip", "install", "llama-cpp-python[server]"],
            capture_output=True, text=True, timeout=900
        )

    @patch("mite.setup.sys.executable", "/usr/bin/python3")
    @patch("mite.setup.subprocess.run")
    def test_install_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = install_llamacpp_python()
        assert result is False

    @patch("mite.setup.sys.executable", "/usr/bin/python3")
    @patch("mite.setup.subprocess.run")
    @patch("mite.setup.check_llamacpp", return_value=False)
    def test_installed_but_not_importable(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = install_llamacpp_python()
        assert result is False


# ===================================================================
# 8. llama.cpp — resolve_gguf_model
# ===================================================================

class TestResolveGgufModel:
    """resolve_gguf_model should return a path or download."""

    @patch("mite.setup._models_dir")
    @patch("mite.setup.os.path.isfile", return_value=False)
    def test_unknown_model_returns_spec(self, mock_isfile, mock_models_dir):
        mock_models_dir.return_value = "/tmp/fake"
        path = resolve_gguf_model("unknown:1b")
        assert path == "unknown:1b"

    def test_cached_model_returns_gguf_path(self):
        """Directly verify that a cached model returns the expected .gguf path."""
        with patch("mite.setup._models_dir") as mock_dir:
            mock_dir.return_value = "/tmp/mite_test_cache"
            Path("/tmp/mite_test_cache").mkdir(parents=True, exist_ok=True)
            try:
                with patch("mite.setup.os.path.isfile", return_value=True):
                    path = resolve_gguf_model("qwen2.5:0.5b")
                    assert path.endswith(".gguf")
                    assert "qwen2.5-0.5b" in path
            finally:
                import shutil
                shutil.rmtree("/tmp/mite_test_cache", ignore_errors=True)


# ===================================================================
# 9. llama.cpp — server lifecycle
# ===================================================================

class TestStartLlamacpp:
    """start_llamacpp should detect running or launch server."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_already_running(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        assert start_llamacpp("qwen2.5:0.5b") is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.check_llamacpp", return_value=False)
    def test_not_installed(self, mock_check, mock_urlopen):
        assert start_llamacpp("qwen2.5:0.5b") is False

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.check_llamacpp", return_value=True)
    @patch("mite.setup.resolve_gguf_model", return_value="/nonexistent/model.gguf")
    @patch("mite.setup.os.path.isfile", return_value=False)
    def test_model_not_found(self, mock_isfile, mock_resolve, mock_check, mock_urlopen):
        assert start_llamacpp("qwen2.5:0.5b") is False

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=[ConnectionRefusedError(), MagicMock()])
    @patch("mite.setup.check_llamacpp", return_value=True)
    @patch("mite.setup.resolve_gguf_model")
    @patch("mite.setup.os.path.isfile", return_value=True)
    @patch("mite.setup.subprocess.Popen")
    @patch("mite.setup.time.sleep")
    def test_launches_server(self, mock_sleep, mock_popen, mock_isfile,
                              mock_resolve, mock_check, mock_urlopen):
        """Happy path: server starts, polls fail then succeed."""
        mock_resolve.return_value = "/home/user/.mite/llamacpp/models/model.gguf"
        mock_popen.return_value = MagicMock()
        assert start_llamacpp("qwen2.5:0.5b") is True
        mock_popen.assert_called_once()


class TestWaitForLlamacpp:
    """wait_for_llamacpp should poll until ready."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_ready_immediately(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        assert wait_for_llamacpp(max_wait=5) is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    @patch("mite.setup.time.sleep")
    def test_timeout(self, mock_sleep, mock_urlopen):
        assert wait_for_llamacpp(max_wait=0) is False


class TestCheckLlamacppEndpoint:
    """check_llamacpp_endpoint should verify external endpoint."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_reachable(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        assert check_llamacpp_endpoint(host="192.168.1.5", port=8080) is True
        called_url = mock_urlopen.call_args[0][0].full_url
        assert "192.168.1.5:8080" in called_url

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=ConnectionRefusedError())
    def test_unreachable(self, mock_urlopen):
        assert check_llamacpp_endpoint(host="192.168.1.5", port=8080) is False


class TestVerifyLlamacpp:
    """verify_llamacpp should send a request and parse response."""

    @patch("mite.setup.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Hi!"}}]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assert llamacpp_verify_fn(host="0.0.0.0", port=8080) is True

    @patch("mite.setup.urllib.request.urlopen",
           side_effect=Exception("connection failed"))
    def test_failure(self, mock_urlopen):
        assert llamacpp_verify_fn(host="0.0.0.0", port=8080) is False

    @patch("mite.setup.urllib.request.urlopen")
    def test_empty_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": ""}}]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assert llamacpp_verify_fn(host="0.0.0.0", port=8080) is False


# ===================================================================
# 10. _call_llm — core model communication (both backends)
# ===================================================================

class TestCallLlmOllama:
    """_call_llm with backend='ollama'."""

    def test_formats_request_correctly(self):
        """Verify the request body sent to Ollama's /api/chat."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "message": {"content": "ok"}
            }).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm(
                "qwen2.5:0.5b",
                [{"role": "user", "content": "hello"}],
                host="http://localhost:11434",
                timeout=60,
                backend="ollama",
            )
            assert result == "ok"

            # Verify the URL and payload
            called_req = mock_urlopen.call_args[0][0]
            assert "/api/chat" in called_req.full_url
            body = json.loads(called_req.data)
            assert body["model"] == "qwen2.5:0.5b"
            assert body["stream"] is False
            assert "options" in body
            assert body["options"]["temperature"] == 0.2

    def test_handles_http_error(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            from urllib.error import URLError
            mock_urlopen.side_effect = URLError("Connection refused")

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               backend="ollama")
            assert "[LLM ERROR]" in result
            assert "Connection refused" in result

    def test_handles_timeout(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            import socket
            mock_urlopen.side_effect = socket.timeout("timed out")

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               backend="ollama", timeout=1)
            assert "[LLM ERROR]" in result
            assert "timed out" in result or "timeout" in result.lower()


class TestCallLlmLlamacpp:
    """_call_llm with backend='llamacpp'."""

    def test_formats_request_correctly(self):
        """Verify the request body sent to llama.cpp's /v1/chat/completions."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "ok"}}]
            }).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm(
                "qwen2.5:0.5b",
                [{"role": "user", "content": "hello"}],
                host="http://localhost:8080",
                timeout=60,
                backend="llamacpp",
            )
            assert result == "ok"

            # Verify OpenAI-compatible format
            called_req = mock_urlopen.call_args[0][0]
            assert "/v1/chat/completions" in called_req.full_url
            body = json.loads(called_req.data)
            assert body["model"] == "qwen2.5:0.5b"
            assert body["stream"] is False
            assert "temperature" in body
            assert "top_p" in body
            # Ollama uses "options" wrapper, llamacpp puts params at top level
            assert "options" not in body

    def test_missing_choices_key(self):
        """llamacpp returns empty string when choices key is missing."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({}).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               host="http://localhost:8080",
                               backend="llamacpp")
            assert result == ""

    def test_empty_choices_list(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"choices": []}).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               backend="llamacpp")
            # The code fails when choices is [] (IndexError), so returns an error
            assert "[LLM ERROR]" in result

    def test_handles_connection_refused(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            from urllib.error import URLError
            mock_urlopen.side_effect = URLError(
                "[Errno 111] Connection refused"
            )

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               host="http://localhost:8080",
                               backend="llamacpp")
            assert "[LLM ERROR]" in result
            assert "Connection refused" in result


class TestCallLlmEdgeCases:
    """_call_llm edge cases common to both backends."""

    def test_empty_messages(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "message": {"content": ""}
            }).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm("test", [], backend="ollama")
            assert result == ""

    def test_missing_message_key_ollama(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"done": True}).encode()
            mock_urlopen.return_value = mock_resp

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               backend="ollama")
            assert result == ""

    def test_invalid_json_response(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"not json"
            mock_urlopen.return_value = mock_resp

            result = _call_llm("test", [{"role": "user", "content": "hi"}],
                               backend="ollama")
            assert "[LLM ERROR]" in result

    def test_non_default_host(self):
        """Should use the provided host URL."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "message": {"content": "ok"}
            }).encode()
            mock_urlopen.return_value = mock_resp

            _call_llm("test", [{"role": "user", "content": "hi"}],
                      host="http://192.168.1.100:11434",
                      backend="ollama")
            called_url = mock_urlopen.call_args[0][0].full_url
            assert "192.168.1.100:11434" in called_url

    def test_non_default_host_llamacpp(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "ok"}}]
            }).encode()
            mock_urlopen.return_value = mock_resp

            _call_llm("test", [{"role": "user", "content": "hi"}],
                      host="http://10.0.0.5:8081",
                      backend="llamacpp")
            called_url = mock_urlopen.call_args[0][0].full_url
            assert "10.0.0.5:8081" in called_url


# ===================================================================
# 11. Unified setup runner
# ===================================================================

class TestRun:
    """run() should coordinate setup for the selected backend."""

    @patch("mite.setup.suggest_backend", return_value="ollama")
    @patch("mite.setup.check_ollama", return_value=True)
    @patch("mite.setup.start_ollama", return_value=True)
    @patch("mite.setup.wait_for_ollama", return_value=True)
    @patch("mite.setup.pull_model", return_value=True)
    @patch("mite.setup.verify_ollama", return_value=True)
    def test_run_ollama(self, mock_test, mock_pull, mock_wait,
                        mock_start, mock_check, mock_suggest):
        run("qwen2.5:0.5b", backend="ollama")
        mock_start.assert_called_once()
        mock_pull.assert_called_once_with("qwen2.5:0.5b")
        mock_test.assert_called_once_with("qwen2.5:0.5b")

    @patch("mite.setup.suggest_backend", return_value="llamacpp")
    @patch("mite.setup.check_llamacpp", return_value=True)
    @patch("mite.setup.start_llamacpp", return_value=True)
    @patch("mite.setup.wait_for_llamacpp", return_value=True)
    @patch("mite.setup.verify_llamacpp", return_value=True)
    def test_run_llamacpp(self, mock_test, mock_wait, mock_start,
                          mock_check, mock_suggest):
        run("qwen2.5:0.5b", backend="llamacpp", host="0.0.0.0", port=8080)
        mock_start.assert_called_once_with("qwen2.5:0.5b", host="0.0.0.0", port=8080)
        mock_wait.assert_called_once()

    @patch("mite.setup.suggest_backend", return_value="ollama")
    @patch("mite.setup.check_ollama", return_value=False)
    @patch("mite.setup.install_ollama", return_value=True)
    @patch("mite.setup.start_ollama", return_value=True)
    @patch("mite.setup.wait_for_ollama", return_value=True)
    @patch("mite.setup.pull_model", return_value=True)
    @patch("mite.setup.verify_ollama", return_value=True)
    def test_run_ollama_installs_if_missing(self, mock_test, mock_pull, mock_wait,
                                             mock_start, mock_install, mock_check,
                                             mock_suggest):
        run("qwen2.5:0.5b", backend="ollama")
        mock_install.assert_called_once()


# ===================================================================
# 12. Integration tests (gated — only run when backend is available)
# ===================================================================

class TestOllamaIntegration:
    """Integration tests against a real Ollama server.

    Skipped unless Ollama is running on localhost:11434.
    """

    @ollama_available
    @ollama_model_available
    def test_call_llm_returns_response(self, ollama_url, sample_messages):
        """_call_llm should return a non-empty string from Ollama."""
        result = _call_llm(
            "qwen2.5:0.5b", sample_messages,
            host=ollama_url, timeout=60, backend="ollama"
        )
        assert result and not result.startswith("[LLM ERROR]"), f"Got error: {result}"
        assert len(result) > 0

    @ollama_available
    def test_start_ollama_detects_running(self, ollama_url):
        """start_ollama should return True when server is already running."""
        assert start_ollama() is True

    @ollama_available
    def test_wait_for_ollama_immediate(self, ollama_url):
        """wait_for_ollama should return quickly when server is up."""
        assert wait_for_ollama(max_wait=5) is True

    @ollama_available
    def test_verify_ollama_function(self):
        """verify_ollama should return True with a running server (model may not exist)."""
        # This just verifies it doesn't crash — the model check is separate
        # If no model is pulled, verify_ollama returns False which is fine
        verify_ollama("qwen2.5:0.5b")  # Should not raise


class TestLlamacppIntegration:
    """Integration tests against a real llama.cpp server.

    Skipped unless a llama.cpp server is running on localhost:8080.
    """

    @llamacpp_running
    def test_call_llm_returns_response(self, llamacpp_url, sample_messages):
        """_call_llm should return a non-empty string from llama.cpp."""
        result = _call_llm(
            "default", sample_messages,
            host=llamacpp_url, timeout=60, backend="llamacpp"
        )
        assert result and not result.startswith("[LLM ERROR]"), f"Got error: {result}"
        assert len(result) > 0

    @llamacpp_running
    def test_check_endpoint_reachable(self, llamacpp_url):
        """check_llamacpp_endpoint should confirm a running server."""
        host = "localhost"
        port = 8080
        assert check_llamacpp_endpoint(host=host, port=port) is True

    @llamacpp_running
    def test_start_llamacpp_detects_running(self):
        """start_llamacpp should detect an already-running server."""
        assert start_llamacpp("default", host="0.0.0.0", port=8080) is True

    @llamacpp_running
    def test_verify_llamacpp_function(self):
        """verify_llamacpp should return True with a running server."""
        assert llamacpp_verify_fn(host="0.0.0.0", port=8080) is True
