"""
tests/test_semantic_similarity.py
───────────────────────────────────
Test-only contracts for Production Increment 2: real local sentence-embedding
semantic similarity, to be implemented in:

    backend/app/policy/semantic_similarity.py

These tests are fully offline and deterministic. They never import, download, or
construct a real SentenceTransformer / torch / Hugging Face / GPU model. Every
behavioural test injects a fake embedding model and patches the future module's
model-load hook.

FUTURE MODULE API CONTRACT (not implemented by this repo, and not implemented
during this task):
    get_semantic_model()                              -> cached model instance
    compute_embedding_similarity(goal_text, action_text) -> float
    compute_embedding_drift(goal_text, action_text)     -> float
    reset_semantic_model_cache()                        -> None

    PLUS an internal, patchable loader hook named  _load_model()  that
    get_semantic_model() calls to construct the model the first time it is
    needed. Tests number --- the loader must be invoked exactly once per cache
    cycle.

SEMANTICS
    Similarity: 1.0 = highly similar · 0.0 = unrelated       (clamped to [0,1])
    Drift:      0.0 = strongly aligned · 1.0 = fully drifted
    drift is defined as clamp01(1.0 - compute_embedding_similarity(...)).

CURRENT STATE: backend/app/policy/semantic_similarity.py does not exist yet, so
the import below raises ImportError and this module fails collection. That
failure is EXPECTED until Production Increment 2 lands.
"""
import math

import pytest

from app.policy import semantic_similarity


# ── Deterministic fake embedding model ──────────────────────────────────────────

class FakeEmbeddingModel:
    """
    Fake embedder with a fixed, user-supplied text→vector map. No ML libraries.
    Records every encode() call so tests can assert batching / reuse.
    """

    def __init__(self, vector_map=None):
        self.vector_map = vector_map or {}
        self.encode_calls: list[list[str]] = []

    def encode(self, texts, **kwargs):
        texts = list(texts)
        self.encode_calls.append(texts)
        vectors = []
        for t in texts:
            if t not in self.vector_map:
                raise KeyError(f"no embedding for {t!r} in the fake map")
            vectors.append(self.vector_map[t])
        return vectors


class FakeBatchModel(FakeEmbeddingModel):
    """Fake embedder that only accepts the goal+action in ONE batch call."""

    def encode(self, texts, **kwargs):
        texts = list(texts)
        if len(texts) != 2:
            raise AssertionError(
                "expected a single batch encode call containing exactly two texts "
                "(goal and action together); got instead {texts!r}"
            )
        return super().encode(texts, **kwargs)


def _patch_model(monkeypatch, vector_map):
    """Install a fake model as the loader target and clear any cached instance."""
    model = FakeEmbeddingModel(vector_map=vector_map)
    monkeypatch.setattr(semantic_similarity, "_load_model", lambda: model)
    semantic_similarity.reset_semantic_model_cache()
    return model


# ── 1. Empty-input handling ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "goal_text,action_text",
    [
        ("", "some action text"),
        ("   ", "some action text"),
        ("\t", "some action text"),
        ("a stated goal", ""),
        ("a stated goal", "   "),
        ("", ""),
        ("  ", "  "),
    ],
)
def test_empty_or_whitespace_input_returns_safety_values_and_loads_no_model(
    monkeypatch, goal_text, action_text
):
    """
    Contract: empty/whitespace inputs must short-circuit to the safe defaults —
    similarity 0.0, drift 1.0 — and the model loader must NOT be invoked.
    """
    calls = {"n": 0}

    def _counting_loader():
        calls["n"] += 1
        return FakeEmbeddingModel()

    monkeypatch.setattr(semantic_similarity, "_load_model", _counting_loader)
    semantic_similarity.reset_semantic_model_cache()

    sim = semantic_similarity.compute_embedding_similarity(goal_text, action_text)
    drift = semantic_similarity.compute_embedding_drift(goal_text, action_text)

    assert sim == 0.0
    assert drift == 1.0
    assert calls["n"] == 0, "model must not be loaded when inputs are empty"


# ── 2. Model-load caching ───────────────────────────────────────────────────────

def test_get_semantic_model_returns_same_cached_instance(monkeypatch):
    """
    Contract: repeated get_semantic_model() calls return the SAME object, and
    the loader (_load_model) is invoked exactly once per cache cycle.
    """
    model = FakeEmbeddingModel()
    calls = {"n": 0}

    def _loader():
        calls["n"] += 1
        return model

    monkeypatch.setattr(semantic_similarity, "_load_model", _loader)
    semantic_similarity.reset_semantic_model_cache()

    first = semantic_similarity.get_semantic_model()
    second = semantic_similarity.get_semantic_model()
    third = semantic_similarity.get_semantic_model()

    assert first is model
    assert second is model
    assert third is model
    assert calls["n"] == 1


# ── 3. Cache reset ──────────────────────────────────────────────────────────────

def test_reset_semantic_model_cache_forces_reload(monkeypatch):
    """
    Contract: reset_semantic_model_cache() clears the cached object so the next
    get_semantic_model() invokes the loader again (new instance).
    """
    calls = {"n": 0}

    def _loader():
        calls["n"] += 1
        return FakeEmbeddingModel()

    monkeypatch.setattr(semantic_similarity, "_load_model", _loader)
    semantic_similarity.reset_semantic_model_cache()

    first = semantic_similarity.get_semantic_model()
    semantic_similarity.reset_semantic_model_cache()
    second = semantic_similarity.get_semantic_model()

    assert first is not second
    assert calls["n"] == 2


# ── 4. Similarity conversion ────────────────────────────────────────────────────

def test_similarity_identical_vectors_near_one(monkeypatch):
    """Identical deterministic vectors → similarity very close to 1.0."""
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [1.0, 0.0]})
    sim = semantic_similarity.compute_embedding_similarity("goal text", "action text")
    assert sim == pytest.approx(1.0, abs=1e-9)


def test_similarity_orthogonal_vectors_near_zero(monkeypatch):
    """Orthogonal vectors → similarity at/around 0.0."""
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [0.0, 1.0]})
    sim = semantic_similarity.compute_embedding_similarity("goal text", "action text")
    assert sim == pytest.approx(0.0, abs=1e-9)


def test_similarity_opposite_vectors_clamped_safely(monkeypatch):
    """
    Opposite vectors → raw cosine is -1.0, which must be clamped safely into
    [0.0, 1.0], i.e. reported as 0.0 (not a negative number).
    """
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [-1.0, 0.0]})
    sim = semantic_similarity.compute_embedding_similarity("goal text", "action text")
    assert sim == 0.0
    assert 0.0 <= sim <= 1.0


def test_similarity_mixed_angle_geometric(monkeypatch):
    """45° split vectors → similarity ≈ cos(45°) ≈ 0.7071."""
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [0.7071, 0.7071]})
    sim = semantic_similarity.compute_embedding_similarity("goal text", "action text")
    assert sim == pytest.approx(math.cos(math.pi / 4), abs=1e-3)


# ── 5. Drift conversion ─────────────────────────────────────────────────────────

def test_drift_identical_vectors_near_zero(monkeypatch):
    """Identical vectors → drift close to 0.0 (strongly aligned)."""
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [1.0, 0.0]})
    drift = semantic_similarity.compute_embedding_drift("goal text", "action text")
    assert drift == pytest.approx(0.0, abs=1e-9)


def test_drift_orthogonal_vectors_near_one(monkeypatch):
    """Orthogonal vectors → drift close to 1.0 (fully drifted)."""
    _patch_model(monkeypatch, {"goal text": [1.0, 0.0], "action text": [0.0, 1.0]})
    drift = semantic_similarity.compute_embedding_drift("goal text", "action text")
    assert drift == pytest.approx(1.0, abs=1e-9)


def test_drift_is_one_minus_similarity_clamped(monkeypatch):
    """
    Contract: compute_embedding_drift(g, a) ≡ clamp01(1.0 - sim).
    """
    goal_vec = [1.0, 0.0]
    action_vec = [-0.7071, 0.7071]
    _patch_model(monkeypatch, {"goal text": goal_vec, "action text": action_vec})
    sim = semantic_similarity.compute_embedding_similarity("goal text", "action text")
    drift = semantic_similarity.compute_embedding_drift("goal text", "action text")
    expected = max(0.0, min(1.0, 1.0 - sim))
    assert drift == pytest.approx(expected, abs=1e-9)


# ── 6. Bounded output ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "goal_vec,action_vec",
    [
        ([1.0, 0.0], [1.0, 0.0]),      # identical
        ([1.0, 0.0], [0.0, 1.0]),      # orthogonal
        ([1.0, 0.0], [-1.0, 0.0]),     # opposite → clamp
        ([1.0, 0.0], [-0.5, 0.8660]),  # obtuse
        ([0.5774, 0.5774, 0.5774], [0.5774, -0.5774, 0.5774]),
    ],
)
def test_similarity_and_drift_always_bounded(monkeypatch, goal_vec, action_vec):
    """Similarity and drift must always be floats inside [0.0, 1.0]."""
    _patch_model(monkeypatch, {"g": goal_vec, "a": action_vec})
    sim = semantic_similarity.compute_embedding_similarity("g", "a")
    drift = semantic_similarity.compute_embedding_drift("g", "a")
    assert isinstance(sim, float)
    assert isinstance(drift, float)
    assert 0.0 <= sim <= 1.0
    assert 0.0 <= drift <= 1.0


# ── 7. Batch encode behaviour ───────────────────────────────────────────────────

def test_goal_and_action_encoded_together_in_one_batch(monkeypatch):
    """
    Contract: where practical the production encodes goal + action in a single
    batch call. A fake model that rejects any call that is not exactly (goal,
    action) proves both strings reach the embedder together.
    """
    # Use the strict model that forces one call with both texts.
    strict = FakeBatchModel({"the real goal": [1.0, 0.0], "the real action": [0.0, 1.0]})
    monkeypatch.setattr(semantic_similarity, "_load_model", lambda: strict)
    semantic_similarity.reset_semantic_model_cache()

    sim = semantic_similarity.compute_embedding_similarity("the real goal", "the real action")

    assert isinstance(sim, float)
    assert strict.encode_calls == [["the real goal", "the real action"]]


# ── 8. Loader failure ───────────────────────────────────────────────────────────

def test_loader_failure_propagates(monkeypatch):
    """
    Contract: when model construction (_load_model) raises, semantic_similarity
    must NOT swallow it nor invent a semantic result — it must propagate the
    exception. evaluate_intent is the layer that catches it and uses lexical
    fallback.
    """
    def _broken_loader():
        raise RuntimeError("model construction failed (offline / unavailable)")

    monkeypatch.setattr(semantic_similarity, "_load_model", _broken_loader)
    semantic_similarity.reset_semantic_model_cache()

    with pytest.raises(RuntimeError):
        semantic_similarity.get_semantic_model()

    with pytest.raises(RuntimeError):
        semantic_similarity.compute_embedding_similarity(
            "order a coffee", "order a latte"
        )

    with pytest.raises(RuntimeError):
        semantic_similarity.compute_embedding_drift(
            "order a coffee", "order a latte"
        )


# ── 9. Model reuse across comparisons ───────────────────────────────────────────

def test_two_comparisons_reuse_same_cached_model(monkeypatch):
    """
    Contract: two separate semantic comparisons must reuse the SAME cached model
    instance (loader invoked once, enrichment reused).
    """
    model = FakeEmbeddingModel(
        {
            "goal a": [1.0, 0.0],
            "action a": [1.0, 0.0],
            "goal b": [0.0, 1.0],
            "action b": [0.0, 1.0],
        }
    )
    calls = {"n": 0}

    def _loader():
        calls["n"] += 1
        return model

    monkeypatch.setattr(semantic_similarity, "_load_model", _loader)
    semantic_similarity.reset_semantic_model_cache()

    s1 = semantic_similarity.compute_embedding_similarity("goal a", "action a")
    s2 = semantic_similarity.compute_embedding_similarity("goal b", "action b")

    assert calls["n"] == 1
    assert semantic_similarity.get_semantic_model() is model
    assert 0.0 <= s1 <= 1.0
    assert 0.0 <= s2 <= 1.0