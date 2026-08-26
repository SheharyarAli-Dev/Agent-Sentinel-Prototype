"""Tests for status module — demo fixture for coding proposal testing."""
from coding_demo.src.status import get_status


def test_get_status() -> None:
    result = get_status()
    assert result == {"status": "ok", "version": "0.1.0"}


def test_get_status_has_version() -> None:
    result = get_status()
    assert "version" in result
