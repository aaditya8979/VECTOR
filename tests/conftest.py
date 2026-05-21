"""
conftest.py — pytest configuration and fixtures for VECTOR test suite.

Markers:
  requires_model:  test needs Qwen model weights locally (skipped in CI)
  requires_ollama: test needs a running Ollama server
  slow:            test takes >10 seconds (skip with: pytest -m 'not slow')
"""
import os
import socket
import pytest
from pathlib import Path


# ── Marker registration ──────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_model: requires Qwen model weights "
        "(auto-skipped in CI if weights are absent)"
    )
    config.addinivalue_line(
        "markers",
        "requires_ollama: requires a running Ollama server on port 11434"
    )
    config.addinivalue_line(
        "markers",
        "slow: takes >10s to run — skip with: pytest -m 'not slow'"
    )


# ── Auto-skip fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _skip_if_no_model(request):
    """Skip tests marked requires_model when weights are absent."""
    if request.node.get_closest_marker("requires_model"):
        mlx_path   = Path(os.path.expanduser("~/models/qwen25-coder-mlx/"))
        llama_path = Path(os.environ.get("TSDC_MODEL_PATH", "_nonexistent_"))
        if not mlx_path.exists() and not llama_path.exists():
            pytest.skip(
                "Model weights not found. "
                "Download to ~/models/qwen25-coder-mlx/ or set TSDC_MODEL_PATH."
            )


@pytest.fixture(autouse=True)
def _skip_if_no_ollama(request):
    """Skip tests marked requires_ollama when server is not reachable."""
    if request.node.get_closest_marker("requires_ollama"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            err = s.connect_ex(("127.0.0.1", 11434))
            if err != 0:
                pytest.skip("Ollama not running. Start with: ollama serve")
        except Exception:
            pytest.skip("Ollama not running. Start with: ollama serve")
        finally:
            s.close()
