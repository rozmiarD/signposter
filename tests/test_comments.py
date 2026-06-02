"""Unit tests for compact Signposter GitHub comment formatters."""

from signposter.comments import (
    TRANSITION_COMMENT_MAX_CHARS,
    audit_github_comment_body,
    contains_auto_close_keyword,
    ensure_github_comment_body,
    format_claim_comment,
    format_complete_comment,
    format_fail_comment,
    format_release_comment,
    redact_github_comment_body,
)


def test_format_claim_comment_basic():
    comment = format_claim_comment(route="worker", gate="ci")

    assert "**Signposter:** claimed task." in comment
    assert "`state:ready → state:active`" in comment
    assert "`route:worker`" in comment
    assert "`gate:ci`" in comment


def test_format_claim_comment_no_gate():
    comment = format_claim_comment(route="reviewer", gate=None)

    assert "`route:reviewer`" in comment
    assert "`gate:" not in comment  # should not include gate line/part


def test_format_release_comment():
    comment = format_release_comment()

    assert "**Signposter:** released task." in comment
    assert "`state:active → state:ready`" in comment
    assert "removed `gate:*`" in comment


def test_format_complete_comment():
    comment = format_complete_comment()

    assert "**Signposter:** completed task." in comment
    assert "`state:active → state:done`" in comment
    assert "issue remains open" in comment


def test_format_fail_comment_no_gates():
    comment = format_fail_comment(removed_gates=False)

    assert "**Signposter:** marked task failed." in comment
    assert "`state:active → state:failed`" in comment
    assert "removed gate" not in comment


def test_format_fail_comment_with_gates_removed():
    comment = format_fail_comment(removed_gates=True)

    assert "**Signposter:** marked task failed." in comment
    assert "`state:active → state:failed`" in comment
    assert "removed gate:*" in comment


def test_comment_audit_accepts_transition_comments():
    comments = [
        format_claim_comment(route="worker", gate="ci"),
        format_release_comment(),
        format_complete_comment(),
        format_fail_comment(removed_gates=True),
    ]

    for comment in comments:
        audit = audit_github_comment_body(comment)
        assert audit.valid
        assert audit.errors == ()
        assert audit.char_count == len(comment)


def test_transition_comments_fit_compact_budget():
    comments = [
        format_claim_comment(route="worker", gate="ci"),
        format_claim_comment(route="reviewer", gate="human"),
        format_release_comment(),
        format_complete_comment(),
        format_fail_comment(removed_gates=True),
    ]

    for comment in comments:
        assert len(comment) <= TRANSITION_COMMENT_MAX_CHARS
        assert len(comment.splitlines()) <= 3


def test_comment_audit_blocks_auto_close_keywords():
    audit = audit_github_comment_body("Signposter report\n\nCloses #123")

    assert not audit.valid
    assert "auto-close keyword" in "; ".join(audit.errors)


def test_comment_audit_blocks_auto_close_issue_variant():
    audit = audit_github_comment_body("Signposter report\n\nResolves issue #123")

    assert not audit.valid
    assert "auto-close keyword" in "; ".join(audit.errors)


def test_comment_audit_blocks_auto_close_past_tense_and_urls():
    samples = [
        "Signposter report\n\nClosed #123",
        "Signposter report\n\nFixed issue #123",
        "Signposter report\n\nResolve https://github.com/acme/project/issues/123",
        "Signposter report\n\nFixes github.com/acme/project#123",
    ]

    for sample in samples:
        assert contains_auto_close_keyword(sample) is True
        audit = audit_github_comment_body(sample)
        assert not audit.valid
        assert "auto-close keyword" in "; ".join(audit.errors)


def test_comment_audit_allows_related_issue_reference():
    body = "Signposter report\n\nRelated issue: #123"

    assert contains_auto_close_keyword(body) is False
    assert audit_github_comment_body(body).valid


def test_comment_audit_blocks_obvious_secret_material():
    token = "github_pat_" + ("A" * 30)
    audit = audit_github_comment_body(f"Signposter report\n\nToken: {token}")

    assert not audit.valid
    assert "possible GitHub token" in "; ".join(audit.errors)


def test_comment_redaction_removes_obvious_secret_material():
    token = "github_pat_" + ("A" * 30)
    body = f"Signposter report\n\nToken: {token}"

    redacted = redact_github_comment_body(body)

    assert token not in redacted
    assert "[REDACTED:github-token]" in redacted
    assert audit_github_comment_body(redacted).valid


def test_comment_ensure_returns_redacted_safe_body():
    token = "sk-" + ("A" * 30)
    body = f"Signposter report\n\nToken: {token}"

    safe = ensure_github_comment_body(body)

    assert token not in safe
    assert "[REDACTED:openai-token]" in safe


def test_comment_redaction_removes_reviewer_token_assignment_value():
    secret = "SIGNPOSTER_REVIEWER_GH_TOKEN=ghp_" + ("A" * 30)
    body = f"Signposter report\n\n{secret}"

    redacted = redact_github_comment_body(body)

    assert "ghp_" not in redacted
    assert "SIGNPOSTER_REVIEWER_GH_TOKEN=" not in redacted
    assert "[REDACTED:reviewer-token-assignment]" in redacted


def test_comment_audit_blocks_unbounded_comment():
    audit = audit_github_comment_body("Signposter\n" + ("x" * 7000))

    assert not audit.valid
    assert "exceeds" in "; ".join(audit.errors)
