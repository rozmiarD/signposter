"""Tests for signposter.doctor module.

Focus on pure / easily testable logic.
External command checks are isolated and can be tested via the result structure.
"""

from __future__ import annotations

from signposter.doctor import (
    CheckStatus,
    check_config_examples_exist,
    check_docs_exist,
    check_pytest_tool,
    check_python_version,
    check_ruff_tool,
    run_doctor_checks,
)


def test_python_version_check_passes_on_modern_python():
    result = check_python_version()
    assert result.status == CheckStatus.OK
    assert "Python 3." in result.message


def test_config_examples_exist_check():
    result = check_config_examples_exist()
    # In the current skeleton all 5 example files should exist
    assert result.status == CheckStatus.OK
    assert "present" in result.message.lower()


def test_docs_exist_check():
    result = check_docs_exist()
    assert result.status == CheckStatus.OK
    assert "present" in result.message.lower()


def test_run_doctor_checks_returns_list_of_results():
    results = run_doctor_checks()
    assert isinstance(results, list)
    assert len(results) >= 8  # At minimum we expect several checks

    names = {r.name for r in results}
    assert "python-version" in names
    assert "config-examples" in names
    assert "docs" in names


def test_doctor_checks_have_expected_statuses():
    results = run_doctor_checks()
    for r in results:
        assert r.status in (CheckStatus.OK, CheckStatus.WARN, CheckStatus.FAIL)
        assert r.name
        assert r.message


def test_pytest_tool_check_detects_venv_install():
    """pytest should be detectable via python -m when installed in the active venv."""
    result = check_pytest_tool()
    assert result.status == CheckStatus.OK
    assert "pytest" in result.message.lower()


def test_ruff_tool_check_detects_venv_install():
    """ruff should be detectable via python -m when installed in the active venv."""
    result = check_ruff_tool()
    assert result.status == CheckStatus.OK
    assert "ruff" in result.message.lower()
