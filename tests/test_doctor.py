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
    check_reviewer_token_present,
    check_ruff_tool,
    check_virtualenv_active,
    format_automation_doctor_report,
    run_automation_doctor_checks,
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


def test_reviewer_token_check_does_not_print_secret(monkeypatch):
    monkeypatch.setenv("SIGNPOSTER_REVIEWER_GH_TOKEN", "secret-token")

    result = check_reviewer_token_present()

    assert result.status == CheckStatus.OK
    assert "secret-token" not in result.message
    assert "present" in result.message


def test_virtualenv_check_returns_structured_status():
    result = check_virtualenv_active()

    assert result.name == "venv"
    assert result.status in (CheckStatus.OK, CheckStatus.WARN)


def test_run_automation_doctor_checks_has_expected_checks():
    results = run_automation_doctor_checks()
    names = {result.name for result in results}

    assert "git-status" in names
    assert "gh-auth" in names
    assert "openclaw-available" in names
    assert "reviewer-token" in names
    assert "venv" in names


def test_format_automation_doctor_report_hides_secret():
    results = [
        type(
            "Result",
            (),
            {
                "name": "reviewer-token",
                "status": CheckStatus.OK,
                "message": "SIGNPOSTER_REVIEWER_GH_TOKEN is present",
                "details": None,
            },
        )()
    ]

    out = format_automation_doctor_report(results)

    assert "Signposter Automation Doctor" in out
    assert "No secrets were printed." in out
    assert "secret-token" not in out
