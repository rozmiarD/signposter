"""Signposter doctor / preflight checks.

Read-only environment and project health checks.
No side effects. Designed to be testable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    details: str | None = None


def check_python_version() -> CheckResult:
    """Check that Python version meets minimum requirements."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version >= (3, 11):
        return CheckResult(
            name="python-version",
            status=CheckStatus.OK,
            message=f"Python {version_str}",
            details=sys.executable,
        )
    else:
        return CheckResult(
            name="python-version",
            status=CheckStatus.FAIL,
            message=f"Python {version_str} (requires >= 3.11)",
        )


def check_working_directory() -> CheckResult:
    """Report current working directory."""
    cwd = Path.cwd()
    return CheckResult(
        name="working-directory",
        status=CheckStatus.OK,
        message=str(cwd),
    )


def check_is_git_repository() -> CheckResult:
    """Check whether current directory is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and "true" in result.stdout.lower():
            return CheckResult(
                name="git-repository",
                status=CheckStatus.OK,
                message="Git repository detected",
            )
        else:
            return CheckResult(
                name="git-repository",
                status=CheckStatus.FAIL,
                message="Not inside a git repository",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return CheckResult(
            name="git-repository",
            status=CheckStatus.FAIL,
            message="git command not available or failed",
        )


def check_git_status() -> CheckResult:
    """Check git working tree status (clean vs dirty)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                name="git-status",
                status=CheckStatus.WARN,
                message="Could not determine git status",
            )

        if result.stdout.strip():
            return CheckResult(
                name="git-status",
                status=CheckStatus.WARN,
                message="Working tree has uncommitted changes",
                details=f"{len(result.stdout.strip().splitlines())} modified files",
            )
        else:
            return CheckResult(
                name="git-status",
                status=CheckStatus.OK,
                message="Working tree is clean",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return CheckResult(
            name="git-status",
            status=CheckStatus.WARN,
            message="Unable to check git status",
        )


def check_command_available(name: str) -> CheckResult:
    """Generic check for presence of a command in PATH."""
    path = shutil.which(name)
    if path:
        return CheckResult(
            name=f"{name}-available",
            status=CheckStatus.OK,
            message=f"{name} found",
            details=path,
        )
    else:
        return CheckResult(
            name=f"{name}-available",
            status=CheckStatus.WARN,
            message=f"{name} not found in PATH",
        )


def check_reviewer_token_present() -> CheckResult:
    """Check reviewer token presence without printing the token."""
    if os.environ.get("SIGNPOSTER_REVIEWER_GH_TOKEN"):
        return CheckResult(
            name="reviewer-token",
            status=CheckStatus.OK,
            message="SIGNPOSTER_REVIEWER_GH_TOKEN is present",
        )
    return CheckResult(
        name="reviewer-token",
        status=CheckStatus.WARN,
        message="SIGNPOSTER_REVIEWER_GH_TOKEN is not set",
    )


def check_virtualenv_active() -> CheckResult:
    """Check whether Signposter appears to be running from a virtualenv."""
    if sys.prefix != sys.base_prefix:
        return CheckResult(
            name="venv",
            status=CheckStatus.OK,
            message="virtualenv is active",
            details=sys.prefix,
        )
    return CheckResult(
        name="venv",
        status=CheckStatus.WARN,
        message="virtualenv is not active",
    )


def check_gh_auth() -> CheckResult:
    """Check gh CLI authentication status (read-only)."""
    gh_path = shutil.which("gh")
    if not gh_path:
        return CheckResult(
            name="gh-auth",
            status=CheckStatus.WARN,
            message="gh not installed — skipping auth check",
        )

    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        output = (result.stdout + result.stderr).lower()

        if "logged in" in output:
            return CheckResult(
                name="gh-auth",
                status=CheckStatus.OK,
                message="gh is authenticated",
            )
        else:
            return CheckResult(
                name="gh-auth",
                status=CheckStatus.WARN,
                message="gh is not authenticated",
                details="Run 'gh auth login' when ready",
            )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return CheckResult(
            name="gh-auth",
            status=CheckStatus.WARN,
            message="Could not determine gh auth status",
        )


def check_pytest_tool() -> CheckResult:
    """Check pytest via the active Python interpreter (python -m pytest --version).

    This is the preferred method for venv-based projects.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output example: "pytest 9.0.3"
            version_line = result.stdout.strip().splitlines()[0]
            return CheckResult(
                name="pytest",
                status=CheckStatus.OK,
                message=version_line,
            )
    except Exception:
        pass

    return CheckResult(
        name="pytest",
        status=CheckStatus.WARN,
        message="pytest not available via python -m pytest",
    )


def check_ruff_tool() -> CheckResult:
    """Check ruff via the active Python interpreter (python -m ruff --version).

    This is the preferred method for venv-based projects.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output example: "ruff 0.15.14"
            version_line = result.stdout.strip().splitlines()[0]
            return CheckResult(
                name="ruff",
                status=CheckStatus.OK,
                message=version_line,
            )
    except Exception:
        pass

    return CheckResult(
        name="ruff",
        status=CheckStatus.WARN,
        message="ruff not available via python -m ruff",
    )


def check_config_examples_exist() -> CheckResult:
    """Verify that example configuration files exist."""
    root = Path.cwd()
    config_dir = root / "configs"

    required = [
        "repos.example.yaml",
        "routing.example.yaml",
        "labels.example.yaml",
        "agents.example.yaml",
        "scheduler.example.yaml",
    ]

    missing = [f for f in required if not (config_dir / f).exists()]

    if not missing:
        return CheckResult(
            name="config-examples",
            status=CheckStatus.OK,
            message="All example config files present",
        )
    else:
        return CheckResult(
            name="config-examples",
            status=CheckStatus.WARN,
            message="Missing example config files",
            details=", ".join(missing),
        )


def check_docs_exist() -> CheckResult:
    """Verify that core documentation files exist."""
    root = Path.cwd()
    docs_dir = root / "docs"

    required = [
        "architecture.md",
        "workflow.md",
        "labels.md",
        "state-machine.md",
    ]

    missing = [f for f in required if not (docs_dir / f).exists()]

    if not missing:
        return CheckResult(
            name="docs",
            status=CheckStatus.OK,
            message="Core documentation files present",
        )
    else:
        return CheckResult(
            name="docs",
            status=CheckStatus.WARN,
            message="Missing documentation files",
            details=", ".join(missing),
        )


def run_doctor_checks() -> list[CheckResult]:
    """Run all preflight checks and return structured results.

    This function is the main entry point for both CLI and tests.
    """
    results: list[CheckResult] = [
        check_python_version(),
        check_working_directory(),
        check_is_git_repository(),
        check_git_status(),
        check_gh_auth(),
        check_pytest_tool(),
        check_ruff_tool(),
        check_config_examples_exist(),
        check_docs_exist(),
        check_command_available("gh"),
        check_command_available("openclaw"),
    ]
    return results


def run_automation_doctor_checks() -> list[CheckResult]:
    """Run read-only automation prerequisite checks."""
    return [
        check_working_directory(),
        check_is_git_repository(),
        check_git_status(),
        check_virtualenv_active(),
        check_command_available("gh"),
        check_gh_auth(),
        check_command_available("openclaw"),
        check_reviewer_token_present(),
    ]


def format_check(result: CheckResult) -> str:
    """Format a single check result for human-readable output."""
    status_symbol = {
        CheckStatus.OK: "✓",
        CheckStatus.WARN: "⚠",
        CheckStatus.FAIL: "✗",
    }.get(result.status, "?")

    line = f"{status_symbol} {result.name}: {result.message}"
    if result.details:
        line += f"  ({result.details})"
    return line


def print_doctor_report(results: list[CheckResult]) -> None:
    """Print a nicely formatted doctor report."""
    print("Signposter Doctor — Environment Preflight Check\n")

    for result in results:
        print(format_check(result))

    # Summary
    ok = sum(1 for r in results if r.status == CheckStatus.OK)
    warn = sum(1 for r in results if r.status == CheckStatus.WARN)
    fail = sum(1 for r in results if r.status == CheckStatus.FAIL)

    print(f"\nSummary: {ok} OK, {warn} warnings, {fail} failures")

    if fail > 0:
        print("\nSome critical checks failed. Address them before proceeding.")
    elif warn > 0:
        print("\nEnvironment is mostly ready, but some warnings exist.")
    else:
        print("\nEnvironment looks good for Signposter development.")


def format_automation_doctor_report(results: list[CheckResult]) -> str:
    """Format automation doctor checks without exposing secrets."""
    lines = ["Signposter Automation Doctor", ""]
    lines.extend(format_check(result) for result in results)
    ok = sum(1 for r in results if r.status == CheckStatus.OK)
    warn = sum(1 for r in results if r.status == CheckStatus.WARN)
    fail = sum(1 for r in results if r.status == CheckStatus.FAIL)
    lines.extend(
        [
            "",
            f"Summary: {ok} OK, {warn} warnings, {fail} failures",
            "",
            "Notes:",
            "  Read-only automation preflight.",
            "  No secrets were printed.",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Run doctor checks and print report. Returns exit code."""
    results = run_doctor_checks()
    print_doctor_report(results)

    has_fail = any(r.status == CheckStatus.FAIL for r in results)
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
