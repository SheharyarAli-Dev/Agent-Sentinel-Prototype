"""
app/models/coding_proposal.py
─────────────────────────────
Structured coding-action contract for the bounded coding demo.

This module defines the CodingProposal schema that represents a file-write
proposal within the isolated coding-demo repository. It is NOT a generic
file-edit model — it is bounded to the coding-demo fixture and integrates
with the existing OperationORM lifecycle.

Design constraints:
  - Only action_type="file_write" is supported in Stage 1.
  - All paths are relative to coding-demo/ root.
  - The coding-demo directory is a fixture/template only.
  - Future execution must operate on a generated runtime copy outside
    the real ASENT source tree.
  - No file writes, patches, or execution occur in Stage 1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Constants ──────────────────────────────────────────────────────────────────

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Maximum content size in characters (configurable via coding_path_rules.json)
_DEFAULT_MAX_CONTENT_SIZE = 50_000

# Demo repository root name (relative to ASENT project root)
CODING_DEMO_ROOT = "coding-demo"


# ── Exceptions ─────────────────────────────────────────────────────────────────

class PathSafetyRejection(Exception):
    """Raised when a path violates safety rules."""

    def __init__(self, reason: str, rule: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


# ── CodingProposal Pydantic model ──────────────────────────────────────────────

class CodingProposal(BaseModel):
    """
    A structured file-write proposal for the bounded coding demo.

    Bounded to the coding-demo repository. All paths are relative
    to coding-demo/ root. No absolute paths, no symlink escapes,
    no traversal outside the repository.
    """
    action_type: Literal["file_write"] = Field(
        ...,
        description="Type of file action. Stage 1 supports only 'file_write'.",
    )
    relative_path: str = Field(
        ...,
        description="Target file path relative to coding-demo/ root.",
        examples=["src/status.py", "config/app.json"],
    )
    expected_old_hash: str = Field(
        ...,
        description="SHA-256 hex digest of the current file content.",
    )
    new_content: str = Field(
        ...,
        description="Proposed new file content.",
    )
    expected_new_hash: str = Field(
        ...,
        description="SHA-256 hex digest of new_content. Must match computed hash.",
    )
    test_profile: Literal["unit", "none"] = Field(
        ...,
        description="Which test suite validates the change.",
    )
    protected_invariants: list[str] = Field(
        default_factory=list,
        description="Invariants that must hold after execution.",
    )

    @field_validator("expected_old_hash")
    @classmethod
    def _validate_old_hash(cls, v: str) -> str:
        if not _HEX64.match(v):
            raise ValueError(
                f"expected_old_hash must be a lowercase 64-character hex string, "
                f"got {v[:16]}... ({len(v)} chars)"
            )
        return v

    @field_validator("expected_new_hash")
    @classmethod
    def _validate_new_hash(cls, v: str) -> str:
        if not _HEX64.match(v):
            raise ValueError(
                f"expected_new_hash must be a lowercase 64-character hex string, "
                f"got {v[:16]}... ({len(v)} chars)"
            )
        return v

    @field_validator("relative_path")
    @classmethod
    def _validate_relative_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("relative_path must not be empty")
        return v.strip()

    @field_validator("new_content")
    @classmethod
    def _validate_new_content(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("new_content must not contain null bytes")
        return v

    @model_validator(mode="after")
    def _validate_hash_matches_content(self) -> "CodingProposal":
        """Verify expected_new_hash matches the SHA-256 of new_content."""
        computed = hashlib.sha256(self.new_content.encode("utf-8")).hexdigest()
        if self.expected_new_hash != computed:
            raise ValueError(
                f"expected_new_hash does not match SHA-256 of new_content. "
                f"Expected {computed[:16]}..., got {self.expected_new_hash[:16]}..."
            )
        return self

    def compute_new_hash(self) -> str:
        """Compute SHA-256 hex digest of new_content."""
        return hashlib.sha256(self.new_content.encode("utf-8")).hexdigest()

    def verify_new_hash(self) -> bool:
        """Verify that expected_new_hash matches the computed hash of new_content."""
        return self.expected_new_hash == self.compute_new_hash()


# ── Path classification ────────────────────────────────────────────────────────

def _load_path_rules() -> dict[str, Any]:
    """Load path classification rules from coding_path_rules.json."""
    rules_path = Path(__file__).parent.parent.parent / "data" / "coding_path_rules.json"
    if not rules_path.exists():
        return {}
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _matches_pattern(path_str: str, pattern: str) -> bool:
    """
    Check if a normalized forward-slash path matches a glob pattern.

    Supports:
      - Directory prefix: "config/**" matches "config/app.json"
      - Double-star: "**/*secret*" matches "path/to/secret_file.txt"
      - Extension: "*.py" matches "src/status.py"
      - Exact: ".env" matches ".env"
      - Containment: *secret* matches any path containing "secret"
    """
    path_lower = path_str.lower()
    pattern_lower = pattern.lower()

    # Exact match
    if path_lower == pattern_lower:
        return True

    # Directory prefix match: "config/**" matches "config/anything"
    if pattern_lower.endswith("/**"):
        prefix = pattern_lower[:-3]
        if path_lower.startswith(prefix + "/") or path_lower == prefix:
            return True

    # Handle patterns with **
    if "**" in pattern_lower:
        # Remove **/ prefix and ** to get the inner pattern
        clean = pattern_lower.replace("**/", "").replace("**", "")
        if clean.startswith("*") and clean.endswith("*"):
            # Containment: *foo* matches if foo is anywhere in the path
            substring = clean[1:-1]
            if substring in path_lower:
                return True
        elif clean.startswith("*"):
            # Suffix match: *foo matches files ending in foo
            suffix = clean[1:]
            if path_lower.endswith(suffix):
                return True
        elif clean.endswith("*"):
            # Prefix match: foo* matches paths starting with foo
            prefix = clean[:-1]
            if path_lower.startswith(prefix):
                return True
        elif path_lower == clean:
            return True

    # Patterns without ** but with wildcards
    if "*" in pattern_lower:
        # Simple glob: "*.py" matches "foo.py"
        if pattern_lower.startswith("*."):
            ext = pattern_lower[1:]  # e.g. ".py"
            filename = PurePosixPath(path_lower).name
            if filename.endswith(ext):
                return True
            if path_lower.endswith(ext):
                return True
        # Containment: *secret* (without **) should also work
        if pattern_lower.startswith("*") and pattern_lower.endswith("*"):
            substring = pattern_lower[1:-1]
            if substring in path_lower:
                return True
        elif pattern_lower.startswith("*"):
            suffix = pattern_lower[1:]
            if path_lower.endswith(suffix):
                return True
        elif pattern_lower.endswith("*"):
            prefix = pattern_lower[:-1]
            if path_lower.startswith(prefix):
                return True

    return False


def classify_path(relative_path: str, rules: dict[str, Any] | None = None) -> str:
    """
    Classify a relative path against path rules.

    Precedence (first match wins):
      1. Structural rejection (handled by validate_coding_path)
      2. Protected -> "protected"
      3. Sensitive -> "sensitive"
      4. Allowed -> "allowed"
      5. Unmatched -> "sensitive" (default WARN, not ALLOW)

    Returns: "protected", "sensitive", or "allowed"
    """
    if rules is None:
        rules = _load_path_rules()

    tiers = rules.get("tiers", {})

    # Check tiers in order: protected -> sensitive -> allowed
    for tier_name in ("protected", "sensitive", "allowed"):
        tier = tiers.get(tier_name, {})
        paths = tier.get("paths", [])
        patterns = tier.get("patterns", [])

        # Check exact path matches
        norm_path = relative_path.replace("\\", "/").lower()
        for p in paths:
            p_norm = p.lower()
            # Directory prefix: "config/**" matches "config/app.json"
            if p_norm.endswith("/**"):
                prefix = p_norm[:-3]
                if norm_path.startswith(prefix + "/") or norm_path == prefix:
                    return tier_name
            # Exact or substring match
            if p_norm in norm_path or norm_path.startswith(p_norm):
                return tier_name

        # Check glob patterns
        for pat in patterns:
            if _matches_pattern(norm_path, pat):
                return tier_name

    # Unmatched defaults to sensitive (WARN)
    return "sensitive"


def validate_coding_path(
    relative_path: str,
    repo_root: str | Path,
) -> str:
    """
    Validate a relative path against all safety rules.

    Structural rejection rules (applied first):
      - Empty path
      - Absolute Unix paths (/...)
      - Windows drive paths (C:\\...)
      - UNC paths (\\\\server\\share)
      - Dot-dot traversal (../...)
      - Null bytes
      - Path resolving outside repo_root (symlink escape)

    Returns: classification tier ("protected", "sensitive", or "allowed")
    Raises: PathSafetyRejection on any structural violation
    """
    if not relative_path or not relative_path.strip():
        raise PathSafetyRejection("Path must not be empty", "empty_path")

    path = relative_path.strip()

    # Reject null bytes
    if "\x00" in path:
        raise PathSafetyRejection(
            "Path must not contain null bytes", "null_byte"
        )

    # Reject absolute Unix paths
    if path.startswith("/"):
        raise PathSafetyRejection(
            f"Absolute Unix path rejected: {path}", "absolute_path"
        )

    # Reject Windows drive paths (C:\, D:\, etc.)
    if re.match(r"^[A-Za-z]:", path):
        raise PathSafetyRejection(
            f"Windows drive path rejected: {path}", "windows_drive_path"
        )

    # Reject UNC paths (\\server\share)
    if path.startswith("\\\\"):
        raise PathSafetyRejection(
            f"UNC path rejected: {path}", "unc_path"
        )

    # Reject dot-dot traversal
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        raise PathSafetyRejection(
            f"Dot-dot traversal rejected: {path}", "dotdot_traversal"
        )

    # Normalize separators to forward slash for classification
    norm_path = path.replace("\\", "/")

    # Resolve against repo_root and check containment using os.path.commonpath
    root = Path(repo_root).resolve()
    try:
        candidate = (root / norm_path).resolve()
    except (OSError, ValueError) as exc:
        raise PathSafetyRejection(
            f"Path cannot be resolved: {path} ({exc})", "resolve_failure"
        )

    # Containment check using os.path.commonpath
    try:
        common = os.path.commonpath([str(root), str(candidate)])
    except ValueError:
        raise PathSafetyRejection(
            f"Path is outside the repository root: {path}", "escape"
        )

    # The common path must be the root (or a parent of root, which shouldn't happen)
    if os.path.normcase(Path(common).resolve()) != os.path.normcase(root):
        raise PathSafetyRejection(
            f"Path resolves outside the repository root: {path}", "escape"
        )

    # Check if path is a symlink (where supported)
    if candidate.is_symlink():
        raise PathSafetyRejection(
            f"Symlink target rejected: {path}", "symlink"
        )

    # Classify using rules
    return classify_path(norm_path)


# ── Canonical fingerprint ──────────────────────────────────────────────────────

def compute_proposal_fingerprint(
    source: str,
    event_type: str,
    agent_identity: str,
    proposal: CodingProposal,
) -> str:
    """
    Compute SHA-256 fingerprint for a coding proposal.

    Binds: source, event_type, agent_identity, action_type, relative_path,
    expected_old_hash, expected_new_hash, test_profile, protected_invariants.

    The fingerprint must securely bind to expected_new_hash, and validation
    proves expected_new_hash matches new_content.

    Returns: 64-character lowercase hex SHA-256 digest.
    """
    canonical = {
        "source": source,
        "event_type": event_type,
        "agent_identity": agent_identity,
        "action_type": proposal.action_type,
        "relative_path": proposal.relative_path.replace("\\", "/"),
        "expected_old_hash": proposal.expected_old_hash,
        "expected_new_hash": proposal.expected_new_hash,
        "test_profile": proposal.test_profile,
        "protected_invariants": sorted(proposal.protected_invariants),
    }
    canonical_json = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_coding_canonical_json(
    source: str,
    event_type: str,
    agent_identity: str,
    proposal: CodingProposal,
    original_goal: str | None = None,
) -> str:
    """
    Build canonical JSON for a coding proposal (for storage in OperationORM).

    Includes new_content since it is the substance of the proposed action.
    The fingerprint binds expected_new_hash which is verified against new_content.
    """
    canonical = {
        "source": source,
        "event_type": event_type,
        "agent_identity": agent_identity,
        "action_type": proposal.action_type,
        "relative_path": proposal.relative_path.replace("\\", "/"),
        "expected_old_hash": proposal.expected_old_hash,
        "new_content": proposal.new_content,
        "expected_new_hash": proposal.expected_new_hash,
        "test_profile": proposal.test_profile,
        "protected_invariants": sorted(proposal.protected_invariants),
    }
    if original_goal:
        canonical["original_goal"] = original_goal
    return json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
