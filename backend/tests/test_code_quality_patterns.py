"""
tests/test_code_quality_patterns.py
──────────────────────────────────────
Unit tests for Module 7b — Code Quality Pattern Checker
(policy/code_quality_patterns.py).
"""
import pytest

from app.policy.code_quality_patterns import check_code_quality, PatternMatch


def test_nested_loop_detected():
    code = """
for i in range(len(data)):
    for j in range(len(data)):
        if data[i] == data[j]:
            count += 1
"""
    matches = check_code_quality(code)
    names = [m.name for m in matches]
    assert "nested-loop-over-large-collection" in names
    for m in matches:
        assert m.suggested_fix.strip() != ""


def test_manual_bubble_sort_detected():
    code = """
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr
"""
    matches = check_code_quality(code)
    names = [m.name for m in matches]
    assert "manual-sort-reinventing-stdlib" in names
    assert any("sorted()" in m.suggested_fix for m in matches)


def test_hardcoded_url_detected():
    code = """
API_URL = "https://api.example.com:8080/v1/data"
TIMEOUT = 30
"""
    matches = check_code_quality(code)
    names = [m.name for m in matches]
    assert "hardcoded-values-that-look-like-config" in names
    assert any("config file" in m.suggested_fix for m in matches)


def test_clean_code_no_matches():
    code = """
def process(items):
    return sorted(items, key=lambda x: x.value)
"""
    matches = check_code_quality(code)
    assert matches == []


def test_empty_code_no_matches():
    matches = check_code_quality("")
    assert matches == []


def test_check_code_quality_returns_list():
    result = check_code_quality("x = 1")
    assert isinstance(result, list)
