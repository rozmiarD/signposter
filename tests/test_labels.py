"""Tests for HARDENING-023A — repository label preflight check."""

from __future__ import annotations

from unittest.mock import patch

from signposter.labels import (
    REQUIRED_LABELS,
    LabelEnsureResult,
    check_labels,
    format_label_check,
    format_label_ensure,
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


def test_ensure_functions_exist_for_h023b():
    """H023B added guarded ensure functions (still read-only by default)."""
    import signposter.labels as labels_mod

    public_names = [n for n in dir(labels_mod) if not n.startswith("_")]
    assert "check_labels" in public_names
    assert "ensure_labels" in public_names
    assert "plan_label_ensure" in public_names
    assert "format_label_ensure" in public_names


# =============================================================================
# H023B-A: Strengthened mutation-path tests for ensure apply
# =============================================================================


def test_ensure_dry_run_missing_labels_does_not_call_create():
    """dry-run with missing labels must never call gh label create."""
    labels_without_merged = [label for label in REQUIRED_LABELS if label != "state:merged"]
    fake_labels = [{"name": label} for label in labels_without_merged]

    with patch("signposter.labels.subprocess.run") as mock_run:
        # First call is the check inside plan_label_ensure
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        from signposter.labels import plan_label_ensure
        result = plan_label_ensure("owner/repo")

        # plan_label_ensure must only have called the list command, never create
        create_calls = [
            c for c in mock_run.call_args_list
            if c[0] and c[0][0] and c[0][0][1] == "label" and c[0][0][2] == "create"
        ]
        assert len(create_calls) == 0
        assert result.status == "ready"
        assert "state:merged" in result.missing_before


def test_ensure_apply_creates_only_missing_labels():
    """apply must call gh label create exactly for the missing labels, with correct shape."""
    labels_without_two = [
        label for label in REQUIRED_LABELS
        if label not in ("state:merged", "risk:low")
    ]
    fake_labels = [{"name": label} for label in labels_without_two]

    with patch("signposter.labels.subprocess.run") as mock_run:
        # First call = label list (inside plan)
        # Subsequent calls = the creates
        call_count = [0]

        def fake_run(cmd, **kwargs):
            if cmd[1] == "label" and cmd[2] == "list":
                call_count[0] += 1
                return type("obj", (object,), {
                    "returncode": 0,
                    "stdout": __import__("json").dumps(fake_labels),
                    "stderr": "",
                })()
            # create calls
            return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

        mock_run.side_effect = fake_run

        from signposter.labels import ensure_labels
        result = ensure_labels("owner/repo", apply=True)

        # We expect exactly 2 create calls
        create_calls = [
            c for c in mock_run.call_args_list
            if len(c[0]) > 0 and c[0][0][2] == "create"
        ]
        assert len(create_calls) == 2

        # Verify command shape for the creates
        for call in create_calls:
            cmd = call[0][0]
            assert cmd[0] == "gh"
            assert cmd[1] == "label"
            assert cmd[2] == "create"
            assert "-R" in cmd
            assert "owner/repo" in cmd
            assert "--description" in cmd
            assert "--color" in cmd
            # label name must be one of the missing ones
            label_name = cmd[3]
            assert label_name in ("state:merged", "risk:low")

        assert result.status == "completed"
        assert set(result.created) == {"state:merged", "risk:low"}
        assert len(result.failed) == 0


def test_ensure_apply_noop_does_not_call_create_when_all_present():
    """apply with no missing labels must not call gh label create at all."""
    fake_labels = [{"name": label} for label in REQUIRED_LABELS]

    with patch("signposter.labels.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = __import__("json").dumps(fake_labels)
        mock_run.return_value.stderr = ""

        from signposter.labels import ensure_labels
        result = ensure_labels("owner/repo", apply=True)

        create_calls = [
            c for c in mock_run.call_args_list
            if len(c[0]) > 0 and c[0][0][2] == "create"
        ]
        assert len(create_calls) == 0
        assert result.status == "completed"
        assert len(result.created) == 0


def test_ensure_apply_fail_fast_on_first_create_failure():
    """apply must stop on the first failed create and not attempt the rest."""
    labels_without_two = [
        label for label in REQUIRED_LABELS
        if label not in ("state:merged", "risk:low")
    ]
    fake_labels = [{"name": label} for label in labels_without_two]

    with patch("signposter.labels.subprocess.run") as mock_run:
        call_count = {"list": 0, "create": 0}

        def fake_run(cmd, **kwargs):
            if cmd[1] == "label" and cmd[2] == "list":
                call_count["list"] += 1
                return type("obj", (object,), {
                    "returncode": 0,
                    "stdout": __import__("json").dumps(fake_labels),
                    "stderr": "",
                })()
            if cmd[2] == "create":
                call_count["create"] += 1
                if call_count["create"] == 1:
                    # First create fails
                    return type("obj", (object,), {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "fatal: label already exists or permission denied",
                    })()
                # Should never reach here because of fail-fast
                return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

            return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

        mock_run.side_effect = fake_run

        from signposter.labels import ensure_labels
        result = ensure_labels("owner/repo", apply=True)

        # Only one create attempt should have been made
        assert call_count["create"] == 1
        assert result.status == "failed / partial"
        assert len(result.created) == 0
        assert len(result.failed) == 1
        assert "state:merged" in result.failed[0] or "risk:low" in result.failed[0]


def test_ensure_apply_reports_created_before_failure():
    """When a later create fails, previously created labels must be reported."""
    labels_without_three = [
        label for label in REQUIRED_LABELS
        if label not in ("state:merged", "risk:low", "state:failed")
    ]
    fake_labels = [{"name": label} for label in labels_without_three]

    with patch("signposter.labels.subprocess.run") as mock_run:
        create_attempts = []

        def fake_run(cmd, **kwargs):
            if cmd[1] == "label" and cmd[2] == "list":
                return type("obj", (object,), {
                    "returncode": 0,
                    "stdout": __import__("json").dumps(fake_labels),
                    "stderr": "",
                })()
            if cmd[2] == "create":
                label_name = cmd[3]
                create_attempts.append(label_name)
                if label_name == "risk:low":
                    # Second create fails
                    return type("obj", (object,), {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "some error",
                    })()
                return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

            return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

        mock_run.side_effect = fake_run

        from signposter.labels import ensure_labels
        result = ensure_labels("owner/repo", apply=True)

        # Two labels created before the failure on the third one
        assert len(result.created) == 2
        assert "state:merged" in result.created
        assert "state:failed" in result.created
        assert any("risk:low" in f for f in result.failed)
        assert len(result.failed) == 1


def test_ensure_apply_never_calls_edit_or_delete():
    """apply must never issue gh label edit or gh label delete commands."""
    labels_without_one = [label for label in REQUIRED_LABELS if label != "state:merged"]
    fake_labels = [{"name": label} for label in labels_without_one]

    with patch("signposter.labels.subprocess.run") as mock_run:
        def fake_run(cmd, **kwargs):
            if cmd[1] == "label" and cmd[2] == "list":
                return type("obj", (object,), {
                    "returncode": 0,
                    "stdout": __import__("json").dumps(fake_labels),
                    "stderr": "",
                })()
            return type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

        mock_run.side_effect = fake_run

        from signposter.labels import ensure_labels
        ensure_labels("owner/repo", apply=True)

        all_cmds = [c[0][0] for c in mock_run.call_args_list if c[0]]
        for cmd in all_cmds:
            if len(cmd) >= 3 and cmd[1] == "label":
                assert cmd[2] not in ("edit", "delete")


def test_ensure_formatted_output_includes_no_modify_no_delete_notes():
    """Formatted ensure output (both dry-run and apply) must contain the safety notes."""

    # Dry-run ready case
    res = LabelEnsureResult(
        repo="owner/repo",
        missing_before=["state:merged"],
        created=[],
        failed=[],
        status="ready",
        mode="dry_run",
    )
    out = format_label_ensure(res)
    assert "No labels were modified." in out
    assert "No labels were deleted." in out

    # Apply success case
    res2 = LabelEnsureResult(
        repo="owner/repo",
        missing_before=["state:merged"],
        created=["state:merged"],
        failed=[],
        status="completed",
        mode="apply",
    )
    out2 = format_label_ensure(res2)
    assert "No existing labels were modified." in out2
    assert "No labels were deleted." in out2
