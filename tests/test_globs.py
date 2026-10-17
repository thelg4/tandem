from __future__ import annotations

import pytest

from tandem.globs import matches_any


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("package-lock.json", "**/package-lock.json", True),
        ("web/package-lock.json", "**/package-lock.json", True),
        ("web/package-lock.json.bak", "**/package-lock.json", False),
        ("node_modules/x/y.js", "**/node_modules/**", True),
        ("src/node_modules_util.py", "**/node_modules/**", False),
        ("a/b.min.js", "**/*.min.js", True),
        ("a/b/c.py", "*.py", False),
        ("c.py", "*.py", True),
    ],
)
def test_globs(path: str, pattern: str, expected: bool) -> None:
    assert matches_any(path, [pattern]) is expected
