"""
tests/test_semantic_prewarm.py
────────────────────────────────
Deterministic tests for the opt-in semantic-model prewarm hook in
app/main.py (used by demo/scripts/start_demo.ps1).

The prewarm MUST:
  1. be skipped when AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL is absent or != "1"
  2. call the existing semantic model loader/cache when the variable is enabled
  3. never prevent FastAPI startup, even if the loader fails
  4. never construct the real MiniLM model during pytest (conftest fences
     semantic_similarity._load_model; these tests also patch the hook directly)

Importing app.main triggers module-level side effects (table creation) exactly
like the existing endpoint tests, and is safe because the prewarm is gated on an
opt-in environment variable and the loader is fenced by tests/conftest.py.
"""
import pytest

import app.main as main


# ── 1. Skipped when the env variable is absent ─────────────────────────────────

@pytest.mark.parametrize("env_value", [None, "0", "2", "yes", ""])
def test_prewarm_skipped_when_not_enabled(monkeypatch, env_value):
    """Loader must not be called unless the env var is exactly '1'."""
    if env_value is None:
        monkeypatch.delenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", raising=False)
    else:
        monkeypatch.setenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", env_value)

    calls = []

    def _spy_loader():
        calls.append("called")
        return object()

    monkeypatch.setattr(main, "get_semantic_model", _spy_loader)
    main.prewarm_semantic_model()
    assert calls == []


# ── 2. Loader called when enabled ──────────────────────────────────────────────

def test_prewarm_calls_loader_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", "1")
    calls = []
    fake_model = object()

    def _fake_loader():
        calls.append("called")
        return fake_model

    monkeypatch.setattr(main, "get_semantic_model", _fake_loader)
    main.prewarm_semantic_model()
    assert calls == ["called"]


# ── 3. Loader failure does not prevent startup ─────────────────────────────────

def test_prewarm_loader_failure_does_not_raise(monkeypatch):
    """A failing loader must be swallowed: prewarm returns without raising."""
    monkeypatch.setenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", "1")

    def _broken_loader():
        raise RuntimeError("boom: model cannot load")

    monkeypatch.setattr(main, "get_semantic_model", _broken_loader)
    # Must not raise; startup continues with lexical fallback.
    main.prewarm_semantic_model()


# ── 4. No real MiniLM is loaded during tests ───────────────────────────────────

def test_prewarm_respects_fenced_loader(monkeypatch):
    """
    The conftest autouse fixture replaces semantic_similarity._load_model with a
    function that raises RuntimeError("offline test"). When the prewarm calls the
    real get_semantic_model under that fence, it must be swallowed and startup
    must continue - proving no real MiniLM is ever constructed in tests.
    """
    monkeypatch.setenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", "1")

    # Point the prewarm at the real loader/cache (which is fenced by conftest).
    from app.policy import semantic_similarity

    monkeypatch.setattr(main, "get_semantic_model", semantic_similarity.get_semantic_model)
    main.prewarm_semantic_model()  # must not raise and must not load MiniLM

    # And prove the fence is actually in place for this session.
    with pytest.raises(RuntimeError):
        semantic_similarity._load_model()


def test_prewarm_ready_when_loader_returns_model(monkeypatch):
    """Successful prewarm path completes cleanly (no exceptions, model cached)."""
    monkeypatch.setenv("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL", "1")
    fake_model = object()

    def _fake_loader():
        return fake_model

    monkeypatch.setattr(main, "get_semantic_model", _fake_loader)
    main.prewarm_semantic_model()  # must not raise
