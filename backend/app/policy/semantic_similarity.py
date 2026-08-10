"""
app/policy/semantic_similarity.py
----------------------------------
Local sentence-embedding semantic similarity for Intent Verification.

API (matches backend/tests/test_semantic_similarity.py):
    get_semantic_model()                                   -> model instance
    compute_embedding_similarity(goal_text, action_text)   -> float in [0, 1]
    compute_embedding_drift(goal_text, action_text)        -> float in [0, 1]
    reset_semantic_model_cache()                           -> None
    _load_model()                                          -> model instance

Semantics
---------
    Similarity: 1.0 = highly similar, 0.0 = unrelated.
    Drift:      0.0 = strongly aligned, 1.0 = fully drifted.
    drift is clamp01(1.0 - similarity).

Lazy loading
------------
    sentence_transformers / torch / numpy are NOT imported at module import
    time.  The model loader performs a lazy import inside _load_model(), so the
    module is importable even when those libraries are not installed.  The model
    is never constructed during module import.

Cache behavior
--------------
    One process-level model cache is shared by all callers.  get_semantic_model()
    returns the cached instance when present, otherwise calls _load_model(), stores
    the result, and returns it.  A threading lock with double-checked access keeps
    concurrent first requests from loading the model more than once.  Loader
    failures are never silently swallowed and always propagate.

Failure responsibility
----------------------
    If model loading raises, this module propagates the exception.  It must never
    invent a semantic score to conceal a load failure.  Intent Verification is
    responsible for catching the error and activating the lexical fallback.
"""
from __future__ import annotations

import threading
from typing import Any

# Model identifier used by the real loader.  Only resolved lazily inside
# _load_model(); nothing is downloaded at import time.
_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

# Process-level model cache + lock for lazy single-instance loading.
_shared_lock = threading.Lock()
_model_cache: Any | None = None


def _load_model() -> Any:
    """
    Construct (or lazily import) the embedding model.

    Lazy import keeps this module importable without sentence_transformers /
    torch / numpy installed.  Returns an object exposing:
        encode(sentences) -> sequence of embeddings
    so that ``model.encode([goal, action])`` returns two embeddings.
    """
    # Deliberately imported only on first use.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_MODEL_ID)


def get_semantic_model() -> Any:
    """
    Return the process-level cached model instance, loading it on first use.

    Double-checked locking ensures concurrent first requests share a single load.
    Loader failures are not caught here and propagate to the caller.
    """
    global _model_cache

    if _model_cache is not None:
        return _model_cache

    with _shared_lock:
        if _model_cache is None:
            _model_cache = _load_model()
    return _model_cache


def reset_semantic_model_cache() -> None:
    """
    Clear the cached model instance.

    Intended primarily for deterministic tests and is safe to call repeatedly.
    The next get_semantic_model() call will invoke _load_model() again.
    """
    global _model_cache

    with _shared_lock:
        _model_cache = None


def _embed_as_float_vectors(embeddings: Any) -> list[list[float]]:
    """
    Normalise raw encoder output into a list of plain float vectors.

    Accepts numpy arrays, torch tensors, or nested Python sequences: each
    embedding is converted element-by-element to float without requiring numpy.
    """
    if embeddings is None:
        raise ValueError("model returned no embeddings")
    vectors: list[list[float]] = []
    for emb in embeddings:
        vector = [float(x) for x in emb]
        vectors.append(vector)
    return vectors


def _validate_embeddings(vectors: list[list[float]]) -> None:
    """Validate the encoder output shape; raise ValueError on malformed output."""
    if len(vectors) != 2:
        raise ValueError(
            f"expected exactly two embeddings (goal and action); got {len(vectors)}"
        )
    dims = {len(v) for v in vectors}
    if len(dims) != 1:
        raise ValueError("goal and action embeddings have different dimensions")
    dim = next(iter(dims))
    if dim == 0:
        # Zero-dimensional vectors are treated as missing evidence -> similarity 0.
        return
    for v in vectors:
        for x in v:
            if x != x:  # NaN guard
                raise ValueError("embedding contains a non-finite value")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Pure-Python cosine similarity between two equal-length float vectors.

    Zero-length or zero-norm vectors return 0.0 (no usable alignment evidence).
    """
    if not a or not b:
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


def compute_embedding_similarity(goal_text: str, action_text: str) -> float:
    """
    Semantic similarity between a goal sentence and an action sentence.

    Returns:
        float in [0.0, 1.0]; 1.0 = highly similar, 0.0 = unrelated.

    Empty or whitespace inputs short-circuit to 0.0 WITHOUT loading the model.
    Non-empty inputs encode both strings together in a single model call and use
    pure-Python cosine similarity, clamped into [0.0, 1.0].
    """
    if not goal_text or not goal_text.strip() or not action_text or not action_text.strip():
        return 0.0

    model = get_semantic_model()
    raw = model.encode([goal_text, action_text])
    vectors = _embed_as_float_vectors(raw)
    _validate_embeddings(vectors)

    similarity = _cosine_similarity(vectors[0], vectors[1])
    return max(0.0, min(1.0, similarity))


def compute_embedding_drift(goal_text: str, action_text: str) -> float:
    """
    Semantic drift between a goal sentence and an action sentence.

    Returns:
        float in [0.0, 1.0]; 0.0 = strongly aligned, 1.0 = fully drifted.

    Defined as clamp01(1.0 - compute_embedding_similarity(...)).
    """
    similarity = compute_embedding_similarity(goal_text, action_text)
    drift = 1.0 - similarity
    return max(0.0, min(1.0, drift))