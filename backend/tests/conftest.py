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

from app.main import app
from app.policy import semantic_similarity
from app.policy.sequential_behaviour import reset_sessions
from app.policy.attve import clear_seen_transactions
from app.policy.feedback_learning import reset_feedback_store
from app.policy.semantic_similarity import reset_semantic_model_cache
from app.sandbox.simulated_cloud import reset_lock_registry
from app.policy import attve


@pytest.fixture(autouse=True)
def _reset_all_global_state():
    """
    Reset ALL module-level global mutable state before each test.

    This ensures complete test isolation by clearing:
    - sequential_behaviour._SESSIONS (session trajectory tracking)
    - attve._SEEN_TRANSACTION_IDS (duplicate transaction tracking)
    - feedback_learning._STORE (human feedback learning)
    - semantic_similarity._model_cache (MiniLM model cache)
    - simulated_cloud._LOCK_REGISTRY (per-state-file lock registry)
    """
    print(f"[FIXTURE SETUP] Resetting global state for test")
    # Reset sequential behaviour session state
    reset_sessions()

    # Reset ATTVE duplicate transaction tracking
    clear_seen_transactions()

    # Reset feedback learning store
    from app.policy.feedback_learning import reset_feedback_store
    reset_feedback_store()

    # Reset semantic model cache
    from app.policy.semantic_similarity import reset_semantic_model_cache
    reset_semantic_model_cache()

    # Reset simulated cloud lock registry
    from app.sandbox.simulated_cloud import reset_lock_registry
    reset_lock_registry()

    # Reload ATTVE merchant registry to ensure clean state
    attve._load_merchant_registry()

    print(f"[FIXTURE SETUP] Global state reset complete")

    yield

    print(f"[FIXTURE TEARDOWN] Resetting global state after test")
    # Teardown: also reset after test for extra safety
    reset_sessions()
    clear_seen_transactions()
    from app.policy.feedback_learning import reset_feedback_store as rfs
    rfs()
    from app.policy.semantic_similarity import reset_semantic_model_cache as rsmc
    rsmc()
    from app.sandbox.simulated_cloud import reset_lock_registry as rlr
    rlr()
    attve._load_merchant_registry()
    # Clear any dependency overrides that may have been set by test modules
    app.dependency_overrides.clear()
    print(f"[FIXTURE TEARDOWN] Global state reset complete")


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