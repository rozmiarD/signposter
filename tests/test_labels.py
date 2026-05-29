"""Tests for HARDENING-023A — repository label preflight check."""

from __future__ import annotations

from unittest.mock import patch

from signposter.labels import (
    REQUIRED_LABELS,
    check_labels,
    format_label_check,
)


def test_required_labels_constant_is_centralized():
    """The list of required labels must be defined in one place."""
    assert len(REQUIRED_LABELS) == 10
    assert "state:merged" in REQUIRED_LABELS
    assert "phase:build" in REQUIRED_LABELS


def test_check_labels_pass_when_all_present():
    """pass when all required labels exist."""
    fake_labels = [{"name": label} for label in REQUIRED_LABELS]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")

        assert result.status == "pass"
        assert len(result.present) == 10
        assert len(result.missing) == 0
        assert result.error is None


def test_check_labels_blocked_when_one_missing():
    """blocked when one required label is missing."""
    labels_without_merged = [lbl for lbl in REQUIRED_LABELS if lbl != "state:merged"]
    fake_labels = [{"name": label} for label in labels_without_merged]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")

        assert result.status == "blocked — required labels missing"
        assert result.missing == ["state:merged"]
        assert len(result.present) == 9


def test_check_labels_blocked_when_multiple_missing():
    """blocked when multiple labels are missing."""
    fake_labels = [{"name": "state:ready"}, {"name": "phase:build"}]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")

        assert result.status == "blocked — required labels missing"
        assert "state:merged" in result.missing
        assert "risk:low" in result.missing
        assert len(result.missing) > 1


def test_output_lists_present_labels_on_pass():
    """output lists present labels when pass."""
    fake_labels = [{"name": label} for label in REQUIRED_LABELS]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")
        out = format_label_check(result)

        assert "Required labels:" in out
        assert "state:merged: present" in out
        assert "phase:build: present" in out


def test_output_lists_missing_labels_when_blocked():
    """output lists missing labels when blocked."""
    fake_labels = [{"name": "state:ready"}]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")
        out = format_label_check(result)

        assert "Missing labels:" in out
        assert "state:merged" in out


def test_output_contains_no_mutation_notes():
    """output always contains the no-mutation safety notes."""
    fake_labels = [{"name": label} for label in REQUIRED_LABELS]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        result = check_labels("owner/repo")
        out = format_label_check(result)

        assert "Read-only label check only." in out
        assert "No labels were created." in out
        assert "No GitHub mutation was performed." in out


def test_gh_failure_produces_blocked_status_with_bounded_error():
    """gh failure produces blocked/unknown status with bounded error."""
    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "fatal: authentication failed" * 10

        result = check_labels("owner/repo")

        assert "blocked" in result.status
        assert result.error is not None
        assert len(result.error) <= 320  # allow for prefix + bounded stderr


def test_command_is_read_only_by_design():
    """The module only performs read operations (no mutation functions exposed)."""
    import signposter.labels as labels_mod

    # Only check + format functions should exist at module level for this task
    public_names = [n for n in dir(labels_mod) if not n.startswith("_")]
    assert "check_labels" in public_names
    assert "format_label_check" in public_names
    # No create/ensure/apply functions yet (H023B territory)
    assert not any("create" in n.lower() or "ensure" in n.lower() for n in public_names)
