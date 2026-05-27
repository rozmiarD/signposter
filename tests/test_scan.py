"""Tests for signposter.scan module.

Focus on pure classification and parsing logic.
Network-dependent functions are not unit-tested here.
"""

from __future__ import annotations

from signposter.scan import (
    CANDIDATE_LABELS,
    LabeledItem,
    find_candidates,
    get_claimability,
)


def make_item(number: int, labels: list[str], item_type: str = "issue") -> LabeledItem:
    return LabeledItem(
        number=number,
        title=f"Test item #{number}",
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type=item_type,
    )


def test_candidate_labels_constant():
    assert "state:ready" in CANDIDATE_LABELS
    assert "phase:review" in CANDIDATE_LABELS
    assert "gate:human" in CANDIDATE_LABELS
    assert "state:done" not in CANDIDATE_LABELS  # should not be a candidate trigger


def test_find_candidates_detects_state_ready():
    items = [
        make_item(1, ["bug", "state:ready"]),
        make_item(2, ["documentation"]),
    ]
    candidates = find_candidates(items)
    assert len(candidates) == 1
    assert candidates[0].number == 1


def test_find_candidates_detects_multiple_workflow_labels():
    items = [
        make_item(10, ["phase:plan", "area:core"]),
        make_item(11, ["gate:review"]),
        make_item(12, ["enhancement"]),
    ]
    candidates = find_candidates(items)
    assert len(candidates) == 2
    numbers = {c.number for c in candidates}
    assert numbers == {10, 11}


def test_find_candidates_ignores_non_matching():
    items = [
        make_item(99, ["wontfix", "duplicate"]),
        make_item(100, ["state:done"]),
    ]
    candidates = find_candidates(items)
    assert len(candidates) == 0


def test_find_candidates_mixes_issues_and_prs():
    items = [
        make_item(5, ["state:ready"], "issue"),
        make_item(42, ["phase:build"], "pr"),
    ]
    candidates = find_candidates(items)
    assert len(candidates) == 2
    assert {c.item_type for c in candidates} == {"issue", "pr"}


# --- Tests simulating output from gh issue list / gh pr list ---


def test_parse_issue_list_style_labels():
    """Simulate data shape returned by 'gh issue list --json labels'."""
    raw_issue = {
        "number": 7,
        "title": "Something needs review",
        "url": "https://github.com/ExatronOmega/signposter/issues/7",
        "labels": [
            {"name": "state:ready", "color": "ededed"},
            {"name": "gate:review", "color": "ededed"},
        ],
        "state": "OPEN",
    }
    labels = [lbl["name"] for lbl in raw_issue.get("labels", [])]
    item = LabeledItem(
        number=raw_issue["number"],
        title=raw_issue["title"],
        html_url=raw_issue.get("url", ""),
        labels=labels,
        item_type="issue",
    )
    assert "state:ready" in item.labels
    assert "gate:review" in item.labels


def test_parse_pr_list_style_labels():
    """Simulate data shape returned by 'gh pr list --json labels'."""
    raw_pr = {
        "number": 12,
        "title": "Feature work",
        "url": "https://github.com/ExatronOmega/signposter/pull/12",
        "labels": [{"name": "phase:build"}],
        "state": "OPEN",
        "isDraft": False,
    }
    labels = [lbl["name"] for lbl in raw_pr.get("labels", [])]
    item = LabeledItem(
        number=raw_pr["number"],
        title=raw_pr["title"],
        html_url=raw_pr.get("url", ""),
        labels=labels,
        item_type="pr",
    )
    assert item.item_type == "pr"
    assert "phase:build" in item.labels


# --- BOOTSTRAP-019C scan semantics tests ---

def test_get_claimability_ready():
    item = make_item(1, ["state:ready", "phase:review", "role:reviewer"])
    claimable, reason = get_claimability(item)
    assert claimable is True
    assert "state:ready" in reason


def test_get_claimability_active():
    item = make_item(2, ["state:active", "phase:review", "role:reviewer"])
    claimable, reason = get_claimability(item)
    assert claimable is False
    assert "already claimed / active" in reason


def test_get_claimability_done():
    item = make_item(3, ["state:done", "phase:build"])
    claimable, reason = get_claimability(item)
    assert claimable is False
    assert "already completed" in reason


def test_format_scan_uses_workflow_items():
    """The public scan report must use 'Workflow Items' not 'Candidate Items'."""
    from signposter.scan import format_scan_report

    result = {
        "repo": "test/repo",
        "open_issues": 2,
        "open_prs": 0,
        "recent_runs": 3,
        "candidates": [
            make_item(2, ["state:active", "phase:review", "role:reviewer"]),
        ],
        "issues": [],
        "prs": [],
        "runs": [],
    }
    report = format_scan_report(result)
    assert "Workflow Items (1):" in report
    assert "Candidate Items" not in report
    assert "claimable: no" in report
    assert "already claimed / active" in report
