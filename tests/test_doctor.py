"""Tests for signposter.doctor module.

Focus on pure / easily testable logic.
External command checks are isolated and can be tested via the result structure.
"""

from __future__ import annotations

import argparse

from signposter.doctor import (
    CheckStatus,
    build_validation_command_plan,
    check_config_examples_exist,
    check_docs_exist,
    check_gh_auth,
    check_openclaw_runtime_hygiene,
    check_pytest_tool,
    check_python_version,
    check_reviewer_token_present,
    check_ruff_tool,
    check_virtualenv_active,
    format_automation_doctor_report,
    format_validation_command_plan,
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


def test_validation_command_plan_uses_changed_files_for_targeted_commands():
    plan = build_validation_command_plan(
        ["src/signposter/gate.py", "tests/test_gate.py", "tests/test_gate.py"]
    )
    out = format_validation_command_plan(plan)

    assert plan.changed_files == ("src/signposter/gate.py", "tests/test_gate.py")
    assert plan.targeted_ruff == "ruff check src/signposter/gate.py tests/test_gate.py"
    assert plan.targeted_pytest == "python -m pytest tests/test_gate.py -q"
    assert plan.full_ruff == "ruff check ."
    assert plan.full_pytest == "python -m pytest tests/ -q"
    assert "No validation command was executed." in out


def test_validation_command_plan_defaults_without_changed_files():
    plan = build_validation_command_plan()

    assert plan.targeted_ruff == "ruff check ."
    assert plan.targeted_pytest == "python -m pytest tests/ -q"


def test_doctor_validation_cli_outputs_read_only_commands(capsys):
    from signposter.cli import run_doctor

    args = argparse.Namespace(
        automation=False,
        topic="validation",
        changed_file=["tests/test_integration.py"],
    )

    exit_code = run_doctor(args)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Signposter Validation Commands" in out
    assert "ruff check tests/test_integration.py" in out
    assert "python -m pytest tests/test_integration.py -q" in out


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


def test_gh_auth_check_warns_when_not_logged_in_message_contains_logged_in_substring():
    """Regression: 'logged in' must not match 'not logged into'."""
    from unittest.mock import patch

    completed = type(
        "CompletedProcess",
        (),
        {"returncode": 1, "stdout": "", "stderr": "You are not logged into any GitHub hosts.\n"},
    )()

    with patch("signposter.doctor.shutil.which", return_value="/usr/bin/gh"), patch(
        "signposter.doctor.subprocess.run", return_value=completed
    ):
        result = check_gh_auth()

    assert result.status == CheckStatus.WARN
    assert result.message == "gh is not authenticated"


def test_gh_auth_check_ok_when_gh_reports_success_exit_code():
    from unittest.mock import patch

    completed = type(
        "CompletedProcess",
        (),
        {
            "returncode": 0,
            "stdout": "github.com\n  ✓ Logged in to github.com account user (keyring)\n",
            "stderr": "",
        },
    )()

    with patch("signposter.doctor.shutil.which", return_value="/usr/bin/gh"), patch(
        "signposter.doctor.subprocess.run", return_value=completed
    ):
        result = check_gh_auth()

    assert result.status == CheckStatus.OK
    assert result.message == "gh is authenticated"


def test_gh_auth_check_warns_when_exit_code_zero_but_output_denies_login():
    from unittest.mock import patch

    completed = type(
        "CompletedProcess",
        (),
        {
            "returncode": 0,
            "stdout": "",
            "stderr": "You are not logged into any GitHub hosts.\n",
        },
    )()

    with patch("signposter.doctor.shutil.which", return_value="/usr/bin/gh"), patch(
        "signposter.doctor.subprocess.run", return_value=completed
    ):
        result = check_gh_auth()

    assert result.status == CheckStatus.WARN
    assert result.message == "gh is not authenticated"


def test_run_automation_doctor_checks_has_expected_checks():
    results = run_automation_doctor_checks()
    names = {result.name for result in results}

    assert "git-status" in names
    assert "gh-auth" in names
    assert "openclaw-available" in names
    assert "openclaw-runtime-hygiene" in names
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


def test_openclaw_runtime_hygiene_warns_on_policy_drift():
    diagnostics = type(
        "Diag",
        (),
        {
            "available": True,
            "command_ok": True,
            "warnings": ("fallback drift",),
            "default_model": "openai/gpt-5.4",
            "error": None,
        },
    )()

    from unittest.mock import patch

    with patch("signposter.doctor.gather_openclaw_runtime_diagnostics", return_value=diagnostics):
        result = check_openclaw_runtime_hygiene()

    assert result.status == CheckStatus.WARN
    assert "drift" in result.message.lower()
