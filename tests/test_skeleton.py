"""Skeleton test to verify project structure.

This test will be replaced once real functionality is added.
"""

import signposter


def test_package_importable():
    """Verify that the package can be imported."""
    assert signposter.__version__ is not None


def test_version_format():
    """Basic sanity check on version string."""
    assert isinstance(signposter.__version__, str)
    assert "." in signposter.__version__
