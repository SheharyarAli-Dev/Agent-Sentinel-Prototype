# coding-demo

Isolated demo repository for testing the ASENT coding proposal contract.

## Structure

```
coding-demo/
  src/
    status.py              # Safe development file (ALLOW target)
  tests/
    test_status.py         # Test file (WARN target)
  config/
    app.json               # Sensitive configuration (WARN target)
  protected/
    secrets.env            # Protected secret (BLOCK target)
```

## Purpose

This directory is a **fixture/template only**. It provides deterministic
initial content and SHA-256 hashes for the coding proposal contract.

**Important:** Tests must never modify these tracked files directly.
All test operations use temporary copies created by pytest's `tmp_path`
fixture.
