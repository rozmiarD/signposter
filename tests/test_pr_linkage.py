from signposter.pr_linkage import detect_pr_issue_linkage


def test_pr_linkage_prefers_branch_when_body_matches() -> None:
    result = detect_pr_issue_linkage(
        "work/issue-4-test-task",
        "Related issue: #4",
    )

    assert result.associated_issue == 4
    assert result.status == "detected"
    assert result.source == "branch-pattern"
    assert result.confidence == "high"


def test_pr_linkage_prefers_branch_when_generic_body_matches() -> None:
    result = detect_pr_issue_linkage(
        "work/issue-4-test-task",
        "This PR updates issue #4 without using a formal Related issue line.",
    )

    assert result.associated_issue == 4
    assert result.status == "detected"
    assert result.source == "branch-pattern"
    assert result.confidence == "high"


def test_pr_linkage_detects_related_issue_without_branch() -> None:
    result = detect_pr_issue_linkage("feature/test", "Related issue: #12")

    assert result.associated_issue == 12
    assert result.status == "detected"
    assert result.source == "pr-body-related-issue"
    assert result.confidence == "medium"


def test_pr_linkage_detects_generic_issue_reference_without_branch() -> None:
    result = detect_pr_issue_linkage("feature/test", "This PR updates issue #12.")

    assert result.associated_issue == 12
    assert result.status == "detected"
    assert result.source == "pr-body-issue-reference"
    assert result.confidence == "low"


def test_pr_linkage_blocks_ambiguous_branch_and_body() -> None:
    result = detect_pr_issue_linkage(
        "work/issue-4-test-task",
        "Related issue: #5",
    )

    assert result.associated_issue is None
    assert result.status == "ambiguous"
    assert result.source == "ambiguous"
    assert result.ambiguous is True
    assert "branch-pattern=#4" in result.reason
    assert "pr-body-related-issue=#5" in result.reason


def test_pr_linkage_missing_when_no_safe_signal() -> None:
    result = detect_pr_issue_linkage("feature/test", "No linkage here")

    assert result.associated_issue is None
    assert result.status == "missing"
