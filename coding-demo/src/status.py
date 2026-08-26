"""Status module — demo fixture for coding proposal testing."""
from __future__ import annotations


def get_status() -> dict[str, str]:
    """Return the current application status."""
    return {"status": "ok", "version": "0.1.0"}
