"""
tests/conftest.py
-----------------
Centralized offline configuration for the pytest suite.

Autouse fixture: during pytest, app.policy.semantic_similarity._load_model is
patched with a function that raises RuntimeError("offline test"). This is the
single point where a real SentenceTransformer would be constructed (lazy import
inside the production loader), so this guarantee means ordinary unit tests NEVER:

  - load or instantiate the real MiniLM SentenceTransformer model,
  - access Hugging Face, or
  - depend on the local model cache / network.

Behavior:
  - The public API (compute_embedding_similarity / compute_embedding_drift /
    get_semantic_model / reset_semantic_model_cache) is left INTACT, so tests
    of the real math and empty-input short circuits in test_semantic_similarity.py
    still verify the true implementation; only the construction point is fenced.
  - Tests that need a fake model patch _load_model themselves via
    monkeypatch.setattr; that per-test patch shadows this autouse fixture for
    that test, exactly as before this fixture existed.
  - Tests that run evaluate_intent() against the real backend here would hit the
    "model unavailable" RuntimeError and therefore the lexical fallback; they
    should instead stub semantic_similarity.compute_embedding_drift directly.

Scope:
  - pytest only. The standalone benchmark script
    (backend/scripts/evaluate_semantic_intent.py) is run outside pytest and is
    unaffected; it continues to load the real MiniLM model.
"""
import pytest

from app.policy import semantic_similarity


@pytest.fixture(autouse=True)
def _offline_semantic_backend(monkeypatch):
    """Prevent any pytest test from constructing the real MiniLM model."""

    def _unavailable_loader():
        raise RuntimeError(
            "semantic model construction is disabled in tests - "
            "patch semantic_similarity._load_model or "
            "semantic_similarity.compute_embedding_drift to provide a fake"
        )

    monkeypatch.setattr(
        semantic_similarity,
        "_load_model",
        _unavailable_loader,
    )