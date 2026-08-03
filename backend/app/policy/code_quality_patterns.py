"""
app/policy/code_quality_patterns.py
──────────────────────────────────────
Module 7b — Code-Quality Pattern Checker (sub-module of Planning Verification)

IMPORTANT — SCOPE & HEURISTIC DISCLAIMER
──────────────────────────────────────────
This module uses heuristic pattern-matching against a fixed list of known-bad
coding patterns.  It is NOT real static analysis, NOT algorithmic complexity
proof, and NOT an AST-level compiler pass.  Each pattern is a string or regex
detector over raw code snippets.  False positives and false negatives are
expected and acceptable for a prototype demonstration.

Pattern List
────────────
  1. nested-loop-over-large-collection (O(n²)-shaped code)
  2. manual-sort-reinventing-stdlib (bubble sort, selection sort, etc.)
  3. manual-search-reinventing-stdlib (manual linear loop search)
  4. obviously-duplicated-logic (repeated code blocks)
  5. hardcoded-values-that-look-like-config (hardcoded URLs, ports, magic constants)

Entry point: check_code_quality(code: str) -> list[PatternMatch]
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PatternMatch:
    """Represents a single fired code-quality pattern match."""
    name: str
    description: str
    suggested_fix: str


# ── Pattern detection heuristics ───────────────────────────────────────────────

def _detect_nested_loop(code: str) -> bool:
    """Detect nested for-loops over collections."""
    # Match two nested for loops in Python/JS syntax
    pattern = r"for\s+\w+\s+in\s+[\s\S]*?:\s*[\r\n]+\s+for\s+\w+\s+in\s+"
    return bool(re.search(pattern, code, re.MULTILINE))


def _detect_manual_sort(code: str) -> bool:
    """Detect manual sorting algorithms (bubble sort, selection sort, etc.)."""
    code_lower = code.lower()

    # Explicit function names or comments
    if any(kw in code_lower for kw in ["bubble_sort", "bubblesort", "selection_sort", "insertion_sort"]):
        return True

    # Bubble sort pattern: nested loops with element swap
    has_nested_range = bool(re.search(r"for\s+\w+\s+in\s+range\([\s\S]*?:\s*[\r\n]+\s+for\s+\w+\s+in\s+range\(", code))
    has_swap = bool(re.search(r"\[\w+\]\s*,\s*\[\w+\s*\+\s*1\]\s*=\s*\[\w+\s*\+\s*1\]\s*,\s*\[\w+\]", code)) or \
               ("temp =" in code and "arr[" in code)

    return has_nested_range and has_swap


def _detect_manual_search(code: str) -> bool:
    """Detect manual linear search loops."""
    # Look for loop iterating over collection with an equality check and return
    pattern = r"for\s+(\w+)\s+in\s+(\w+):\s*[\r\n]+\s+if\s+\1(?:\.\w+)?\s*==\s*[\w\.\'\"]+:\s*[\r\n]+\s+return"
    return bool(re.search(pattern, code, re.MULTILINE))


def _detect_duplicated_logic(code: str) -> bool:
    """Detect repeated identical lines of non-trivial code (3+ occurrences)."""
    lines = [line.strip() for line in code.splitlines() if len(line.strip()) > 20 and not line.strip().startswith(("#", "//", "def ", "class "))]
    counts: dict[str, int] = {}
    for line in lines:
        counts[line] = counts.get(line, 0) + 1
        if counts[line] >= 3:
            return True
    return False


def _detect_hardcoded_config(code: str) -> bool:
    """Detect hardcoded URLs, port numbers, or API keys."""
    # Match http:// or https:// URLs, or localhost:8000 style strings
    has_url = bool(re.search(r"['\"]https?://[^\s'\"]+['\"]", code))
    has_port = bool(re.search(r"['\"]localhost:\d{4,5}['\"]|['\"]127\.0\.0\.1:\d{4,5}['\"]", code))
    return has_url or has_port


# ── Pattern Registry ───────────────────────────────────────────────────────────

PATTERNS: list[dict] = [
    {
        "name": "nested-loop-over-large-collection",
        "description": "Nested for-loops iterating over collections suggest O(n²) time complexity.",
        "detect": _detect_nested_loop,
        "suggested_fix": (
            "Consider replacing the nested loop with a dictionary/set lookup, list comprehension, "
            "or stdlib function (e.g. itertools, collections.Counter) to reduce complexity to O(n)."
        ),
    },
    {
        "name": "manual-sort-reinventing-stdlib",
        "description": "Manual sorting algorithm implementation (bubble/selection sort) detected.",
        "detect": _detect_manual_sort,
        "suggested_fix": (
            "Replace the manual sorting function with Python's built-in sorted() function or list.sort() method, "
            "which uses Timsort and is significantly faster and safer."
        ),
    },
    {
        "name": "manual-search-reinventing-stdlib",
        "description": "Manual linear search loop detected where a standard library call or set lookup exists.",
        "detect": _detect_manual_search,
        "suggested_fix": (
            "Replace the manual linear search loop with Python's 'in' operator, next() with a generator expression, "
            "or convert the collection to a set/dict for O(1) lookups."
        ),
    },
    {
        "name": "obviously-duplicated-logic",
        "description": "Repeated identical code blocks detected in the snippet.",
        "detect": _detect_duplicated_logic,
        "suggested_fix": (
            "Extract the repeated logic into a named helper function or loop to improve maintainability and avoid duplication."
        ),
    },
    {
        "name": "hardcoded-values-that-look-like-config",
        "description": "Hardcoded configuration values (URLs, ports, magic constants) detected in source code.",
        "detect": _detect_hardcoded_config,
        "suggested_fix": (
            "Move hardcoded configuration values (URLs, ports, magic numbers) to a config file, environment variable, "
            "or named constant module."
        ),
    },
]


def check_code_quality(code: str) -> list[PatternMatch]:
    """
    Run registered code-quality heuristic pattern detectors against a code string.

    Args:
        code: Raw source code text of a single plan step.

    Returns:
        List of PatternMatch objects for all fired patterns.
    """
    if not code or not code.strip():
        return []

    matches: list[PatternMatch] = []
    for p in PATTERNS:
        try:
            if p["detect"](code):
                matches.append(
                    PatternMatch(
                        name=p["name"],
                        description=p["description"],
                        suggested_fix=p["suggested_fix"],
                    )
                )
        except Exception:
            pass

    return matches
