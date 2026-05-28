"""Unit tests for compact Signposter GitHub comment formatters."""

from signposter.comments import (
    format_claim_comment,
    format_complete_comment,
    format_fail_comment,
    format_release_comment,
)


def test_format_claim_comment_basic():
    comment = format_claim_comment(route="worker", gate="ci")

    assert "**Signposter:** claimed task for local worker run." in comment
    assert "`state:ready → state:active`" in comment
    assert "`route:worker`" in comment
    assert "`gate:ci`" in comment


def test_format_claim_comment_no_gate():
    comment = format_claim_comment(route="reviewer", gate=None)

    assert "`route:reviewer`" in comment
    assert "`gate:" not in comment  # should not include gate line/part


def test_format_release_comment():
    comment = format_release_comment()

    assert "**Signposter:** released task back to queue." in comment
    assert "`state:active → state:ready`" in comment
    assert "removed `gate:*`" in comment


def test_format_complete_comment():
    comment = format_complete_comment()

    assert "**Signposter:** completed task." in comment
    assert "`state:active → state:done`" in comment
    assert "issue remains open" in comment


def test_format_fail_comment_no_gates():
    comment = format_fail_comment(removed_gates=False)

    assert "**Signposter:** marked task as failed." in comment
    assert "`state:active → state:failed`" in comment
    assert "removed gate" not in comment


def test_format_fail_comment_with_gates_removed():
    comment = format_fail_comment(removed_gates=True)

    assert "**Signposter:** marked task as failed." in comment
    assert "`state:active → state:failed`" in comment
    assert "removed gate:*" in comment
